"""End-to-end repository tests against the live psycopg3 pool.

Covers every public method on PsycopgProjectRepo, PsycopgMilestoneRepo,
and PsycopgTokenLogRepo. Auto-skips when Postgres is unreachable.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from psycopg_pool import ConnectionPool

from aidevswarm.db.repositories import (
    PsycopgIdeaEvaluationRepo,
    PsycopgMilestoneRepo,
    PsycopgProjectRepo,
    PsycopgTokenLogRepo,
)
from aidevswarm.schemas import (
    AcceptanceCriterion,
    CriticScores,
    IdeaEvaluation,
    MilestoneSpec,
    MilestoneState,
    Project,
    ProjectSpec,
    ProjectState,
)

# ``live_pool`` is provided by tests/integration/conftest.py — it points at
# an isolated ``<base>_test`` database, never the operator's live one.

pytestmark = pytest.mark.integration


def _spec() -> ProjectSpec:
    return ProjectSpec(
        title="t", summary="s", rationale="r", stack=["python"], tags=["x"], score=85
    )


@pytest.fixture
def project(live_pool: ConnectionPool) -> Iterator[Project]:
    repo = PsycopgProjectRepo(live_pool)
    p = repo.create(Project(name=f"repo-test-{uuid4()}", spec=_spec()))
    yield p
    with live_pool.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM projects WHERE id = %s", (str(p.id),))


# ------------------------- ProjectRepo -------------------------


def test_project_create_and_get(live_pool: ConnectionPool, project: Project) -> None:
    repo = PsycopgProjectRepo(live_pool)
    fetched = repo.get(project.id)
    assert fetched is not None
    assert fetched.name == project.name
    assert fetched.spec.title == "t"
    assert fetched.state is ProjectState.QUEUED


def test_project_get_returns_none_for_unknown(live_pool: ConnectionPool) -> None:
    repo = PsycopgProjectRepo(live_pool)
    assert repo.get(uuid4()) is None


def test_project_list_by_state(live_pool: ConnectionPool, project: Project) -> None:
    repo = PsycopgProjectRepo(live_pool)
    queued = repo.list_by_state(ProjectState.QUEUED)
    assert any(p.id == project.id for p in queued)


def test_project_update_state_round_trip(live_pool: ConnectionPool, project: Project) -> None:
    repo = PsycopgProjectRepo(live_pool)
    updated = repo.update_state(project.id, ProjectState.PLANNING)
    assert updated.state is ProjectState.PLANNING
    assert repo.get(project.id).state is ProjectState.PLANNING  # type: ignore[union-attr]


def test_project_update_state_unknown_raises(live_pool: ConnectionPool) -> None:
    repo = PsycopgProjectRepo(live_pool)
    with pytest.raises(LookupError):
        repo.update_state(uuid4(), ProjectState.PLANNING)


_NON_TERMINAL = {
    ProjectState.PLANNING,
    ProjectState.AWAITING_APPROVAL,
    ProjectState.BUILDING,
    ProjectState.REPLANNING,
    ProjectState.INTEGRATION,
}


def test_project_get_active_returns_in_flight_project(
    live_pool: ConnectionPool, project: Project
) -> None:
    # Asserts the get_active CONTRACT without assuming this project is the
    # only in-flight one: get_active picks the oldest-updated non-terminal
    # project, so with concurrent projects it need not be ours.
    repo = PsycopgProjectRepo(live_pool)
    # A QUEUED project is never reported as in flight.
    assert all(p.id != project.id for p in repo.list_by_state(ProjectState.PLANNING))
    repo.update_state(project.id, ProjectState.PLANNING)
    # Now it IS in flight ...
    assert any(p.id == project.id for p in repo.list_by_state(ProjectState.PLANNING))
    # ... and get_active returns *some* non-terminal project.
    active = repo.get_active()
    assert active is not None
    assert active.state in _NON_TERMINAL


def test_project_set_github_repo(live_pool: ConnectionPool, project: Project) -> None:
    repo = PsycopgProjectRepo(live_pool)
    repo.set_github_repo(project.id, "https://github.com/x/y")
    refetched = repo.get(project.id)
    assert refetched is not None
    assert refetched.github_repo == "https://github.com/x/y"


# ------------------------- MilestoneRepo -------------------------


def _ms_spec(title: str) -> MilestoneSpec:
    return MilestoneSpec(
        title=title,
        description="d",
        acceptance_criteria=[AcceptanceCriterion(description="x", verifier="pytest")],
    )


def test_milestone_create_many_then_list_and_next_pending(
    live_pool: ConnectionPool, project: Project
) -> None:
    mrepo = PsycopgMilestoneRepo(live_pool)
    rows = mrepo.create_many(project.id, [_ms_spec("first"), _ms_spec("second"), _ms_spec("third")])
    assert len(rows) == 3
    assert [m.ordinal for m in rows] == [0, 1, 2]
    listed = mrepo.list_for_project(project.id)
    assert [m.title for m in listed] == ["first", "second", "third"]
    nxt = mrepo.next_pending(project.id)
    assert nxt is not None
    assert nxt.title == "first"


def test_milestone_update_state_and_record_attempt(
    live_pool: ConnectionPool, project: Project
) -> None:
    mrepo = PsycopgMilestoneRepo(live_pool)
    [m] = mrepo.create_many(project.id, [_ms_spec("solo")])
    started = mrepo.update_state(m.id, MilestoneState.BUILDING)
    assert started.state is MilestoneState.BUILDING

    # Failure path bumps retry_count, keeps milestone in failed.
    failed = mrepo.record_attempt(m.id, success=False, commit_hash=None)
    assert failed.state is MilestoneState.FAILED
    assert failed.retry_count == 1

    # Success path locks commit_hash + retry_count unchanged.
    succeeded = mrepo.record_attempt(m.id, success=True, commit_hash="deadbeef")
    assert succeeded.state is MilestoneState.DONE
    assert succeeded.commit_hash == "deadbeef"
    assert succeeded.retry_count == 1


def test_requeue_stale_building_recovers_orphaned_milestone(
    live_pool: ConnectionPool, project: Project
) -> None:
    """A milestone orphaned in `building` (mid-build restart) is requeued."""
    mrepo = PsycopgMilestoneRepo(live_pool)
    [a, b] = mrepo.create_many(project.id, [_ms_spec("orphan"), _ms_spec("done-one")])
    mrepo.update_state(a.id, MilestoneState.BUILDING)  # simulate a crash mid-build
    mrepo.record_attempt(b.id, success=True, commit_hash="x")  # b is done

    # Before: next_pending skips the orphaned `building` milestone entirely.
    assert mrepo.next_pending(project.id) is None

    requeued = mrepo.requeue_stale_building()
    assert requeued >= 1

    # After: the orphan is pending again and gets picked up.
    nxt = mrepo.next_pending(project.id)
    assert nxt is not None and nxt.id == a.id
    assert nxt.state is MilestoneState.PENDING


def test_milestone_update_state_unknown_raises(live_pool: ConnectionPool) -> None:
    mrepo = PsycopgMilestoneRepo(live_pool)
    with pytest.raises(LookupError):
        mrepo.update_state(uuid4(), MilestoneState.DONE)


def test_milestone_record_attempt_unknown_raises(live_pool: ConnectionPool) -> None:
    mrepo = PsycopgMilestoneRepo(live_pool)
    with pytest.raises(LookupError):
        mrepo.record_attempt(uuid4(), success=True, commit_hash="x")


def test_milestone_next_pending_returns_none_when_empty(
    live_pool: ConnectionPool, project: Project
) -> None:
    mrepo = PsycopgMilestoneRepo(live_pool)
    assert mrepo.next_pending(project.id) is None


def test_milestone_replace_with_shifts_ordinals_without_collision(
    live_pool: ConnectionPool, project: Project
) -> None:
    """Regression: the bulk ordinal shift must not trip the UNIQUE
    (project_id, ordinal) constraint (needs it DEFERRABLE)."""
    mrepo = PsycopgMilestoneRepo(live_pool)
    mrepo.create_many(project.id, [_ms_spec(f"m{i}") for i in range(4)])  # ordinals 0..3
    first = mrepo.next_pending(project.id)
    assert first is not None
    children = mrepo.replace_with(first.id, [_ms_spec("c1"), _ms_spec("c2")])
    assert len(children) == 2
    listed = mrepo.list_for_project(project.id)
    ordinals = [m.ordinal for m in listed]
    assert len(listed) == 5  # 4 - 1 parent + 2 children
    assert ordinals == sorted(ordinals)
    assert len(ordinals) == len(set(ordinals))  # no duplicates


def test_milestone_insert_after_shifts_ordinals_without_collision(
    live_pool: ConnectionPool, project: Project
) -> None:
    mrepo = PsycopgMilestoneRepo(live_pool)
    mrepo.create_many(project.id, [_ms_spec(f"m{i}") for i in range(4)])
    first = mrepo.next_pending(project.id)
    assert first is not None
    mrepo.insert_after(first.id, _ms_spec("inserted"))
    listed = mrepo.list_for_project(project.id)
    ordinals = [m.ordinal for m in listed]
    assert len(listed) == 5
    assert ordinals == sorted(ordinals)
    assert len(ordinals) == len(set(ordinals))


# ------------------------- TokenLogRepo -------------------------


def test_token_log_record_then_aggregates(live_pool: ConnectionPool, project: Project) -> None:
    mrepo = PsycopgMilestoneRepo(live_pool)
    trepo = PsycopgTokenLogRepo(live_pool)
    [m] = mrepo.create_many(project.id, [_ms_spec("tlog")])

    trepo.record(
        project_id=project.id,
        milestone_id=m.id,
        role="Developer",
        model="claude-opus-4-7",
        input_tokens=100,
        output_tokens=200,
        cost_usd=0.42,
    )
    trepo.record(
        project_id=project.id,
        milestone_id=m.id,
        role="Tester",
        model="claude-opus-4-7",
        input_tokens=50,
        output_tokens=75,
        cost_usd=0.20,
    )

    assert trepo.milestone_total_tokens(m.id) == 425  # 100+200+50+75
    # daily total includes everything from today
    assert trepo.daily_total_tokens() >= 425


def test_token_log_milestone_total_unknown_is_zero(
    live_pool: ConnectionPool,
) -> None:
    trepo = PsycopgTokenLogRepo(live_pool)
    assert trepo.milestone_total_tokens(uuid4()) == 0


def test_token_log_record_with_null_project_milestone(
    live_pool: ConnectionPool,
) -> None:
    trepo = PsycopgTokenLogRepo(live_pool)
    trepo.record(
        project_id=None,
        milestone_id=None,
        role="Ideation",
        model="claude-haiku-4-5",
        input_tokens=10,
        output_tokens=20,
        cost_usd=0.01,
    )
    # No exception is the assertion.


def test_token_log_cost_and_project_aggregates(live_pool: ConnectionPool, project: Project) -> None:
    trepo = PsycopgTokenLogRepo(live_pool)
    trepo.record(
        project_id=project.id,
        milestone_id=None,
        role="Developer",
        model="claude-opus-4-7",
        input_tokens=1000,
        output_tokens=500,
        cost_usd=0.30,
    )
    assert trepo.daily_cost_usd() >= 0.30
    roles = dict((r[0], r[2]) for r in trepo.daily_by_role())
    assert roles.get("Developer", 0.0) >= 0.30
    all_tokens, all_cost = trepo.all_time_totals()
    assert all_tokens >= 1500 and all_cost >= 0.30
    by_proj = {pid: cost for pid, _tok, cost in trepo.by_project()}
    assert by_proj.get(project.id, 0.0) >= 0.30


# ------------------------- status_detail -------------------------


def test_project_pause_round_trips_and_survives_a_new_repo_instance(
    live_pool: ConnectionPool, project: Project
) -> None:
    """Pause is durable — a fresh repo (simulating a process restart) still
    sees the project as paused. This is the bug the move-to-Postgres fixed:
    Redis lost the pause key on every container reset."""
    repo = PsycopgProjectRepo(live_pool)
    assert repo.is_paused(project.id) is False
    repo.set_paused(project.id, True)
    assert repo.is_paused(project.id) is True
    # A new repo instance reading the same DB still sees the pause.
    fresh = PsycopgProjectRepo(live_pool)
    assert fresh.is_paused(project.id) is True
    refetched = fresh.get(project.id)
    assert refetched is not None and refetched.is_paused is True
    repo.set_paused(project.id, False)
    assert repo.is_paused(project.id) is False


def test_set_status_detail_round_trips(live_pool: ConnectionPool, project: Project) -> None:
    prepo = PsycopgProjectRepo(live_pool)
    prepo.set_status_detail(project.id, "blocked: milestone X failed 3x")
    got = prepo.get(project.id)
    assert got is not None and got.status_detail == "blocked: milestone X failed 3x"
    prepo.set_status_detail(project.id, None)
    cleared = prepo.get(project.id)
    assert cleared is not None and cleared.status_detail is None


# ------------------------- IdeaEvaluationRepo -------------------------


def test_idea_evaluation_record_and_list(live_pool: ConnectionPool) -> None:
    repo = PsycopgIdeaEvaluationRepo(live_pool)
    scores = CriticScores(
        depth_ambition=90,
        usefulness_niche=85,
        novelty=80,
        decomposability=85,
        buildability=80,
    )
    stored = repo.record(
        IdeaEvaluation(
            title=f"idea-{uuid4()}",
            summary="s",
            scores=scores,
            total=85,
            accepted=True,
            round=1,
        )
    )
    assert stored.id > 0
    recent = repo.list_recent(limit=10)
    assert any(e.title == stored.title and e.accepted for e in recent)
