"""End-to-end repository tests against the live psycopg3 pool.

Covers every public method on PsycopgProjectRepo, PsycopgMilestoneRepo,
and PsycopgTokenLogRepo. Auto-skips when Postgres is unreachable.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from psycopg_pool import ConnectionPool

from aidevswarm.db.pool import close_pool, open_pool
from aidevswarm.db.repositories import (
    PsycopgMilestoneRepo,
    PsycopgProjectRepo,
    PsycopgTokenLogRepo,
)
from aidevswarm.schemas import (
    AcceptanceCriterion,
    Milestone,
    MilestoneSpec,
    MilestoneState,
    Project,
    ProjectSpec,
    ProjectState,
)
from aidevswarm.settings import Settings

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def live_pool() -> Iterator[ConnectionPool]:
    os.environ.setdefault("AIDEVSWARM_PG_HOST", "localhost")
    settings = Settings()
    try:
        pool = open_pool(settings)
    except Exception as exc:
        pytest.skip(f"Postgres unavailable: {exc}")
    yield pool
    close_pool()


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


def test_project_update_state_round_trip(
    live_pool: ConnectionPool, project: Project
) -> None:
    repo = PsycopgProjectRepo(live_pool)
    updated = repo.update_state(project.id, ProjectState.PLANNING)
    assert updated.state is ProjectState.PLANNING
    assert repo.get(project.id).state is ProjectState.PLANNING  # type: ignore[union-attr]


def test_project_update_state_unknown_raises(live_pool: ConnectionPool) -> None:
    repo = PsycopgProjectRepo(live_pool)
    with pytest.raises(LookupError):
        repo.update_state(uuid4(), ProjectState.PLANNING)


def test_project_get_active_returns_in_flight_project(
    live_pool: ConnectionPool, project: Project
) -> None:
    repo = PsycopgProjectRepo(live_pool)
    # queued isn't "active"
    assert repo.get_active() is None or repo.get_active().id != project.id  # type: ignore[union-attr]
    repo.update_state(project.id, ProjectState.PLANNING)
    active = repo.get_active()
    assert active is not None
    assert active.id == project.id


def test_project_set_github_repo(
    live_pool: ConnectionPool, project: Project
) -> None:
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
    rows = mrepo.create_many(
        project.id, [_ms_spec("first"), _ms_spec("second"), _ms_spec("third")]
    )
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


# ------------------------- TokenLogRepo -------------------------


def test_token_log_record_then_aggregates(
    live_pool: ConnectionPool, project: Project
) -> None:
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
