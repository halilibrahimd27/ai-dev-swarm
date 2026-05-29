"""Unit tests for :class:`CommandRouter`.

Drives each command through the router using in-memory fakes for
``ProjectRepo``, ``SteeringRepo``, and ``KillSwitch``. Asserts both
the side-effects (state transition, note row, kill-switch trip) and
the ``CommandResult`` returned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from aidevswarm.orchestrator.command_router import CommandRouter
from aidevswarm.schemas import (
    AbortProject,
    AcceptanceCriterion,
    Approve,
    DropAndStartNew,
    IdeateNow,
    InjectNote,
    KillSwitch,
    ListState,
    MilestoneSpec,
    PauseProject,
    Project,
    ProjectSpec,
    ProjectState,
    RejectIdea,
    Rescope,
    ResumeProject,
    ShowTranscript,
    SwitchToIdea,
    TransformProject,
)
from aidevswarm.tools.kill_switch import InMemoryKillSwitch
from tests.fakes import InMemoryMilestoneRepo, InMemoryProjectRepo


@dataclass
class _FakeSteeringRepo:
    """In-memory steering repo for the router tests."""

    notes: list[dict[str, Any]] = field(default_factory=list)
    _counter: int = 0

    def add_note(self, project_id: UUID, body: str, *, author: str = "human") -> int:
        self._counter += 1
        self.notes.append(
            {"id": self._counter, "project_id": project_id, "body": body, "author": author}
        )
        return self._counter

    def pull_unconsumed(self, project_id: UUID, role: str) -> list[str]:
        # Not exercised by router tests, but the Protocol needs both methods.
        return []


def _spec() -> ProjectSpec:
    return ProjectSpec(
        title="t", summary="s", rationale="r", stack=["python"], tags=["x"], score=85
    )


def _make_router(
    *,
    project: Project | None = None,
) -> tuple[CommandRouter, InMemoryProjectRepo, _FakeSteeringRepo, InMemoryKillSwitch]:
    project_repo = InMemoryProjectRepo()
    if project is not None:
        project_repo.create(project)
    steering = _FakeSteeringRepo()
    kill = InMemoryKillSwitch()
    router = CommandRouter(project_repo=project_repo, steering_repo=steering, kill_switch=kill)
    return router, project_repo, steering, kill


# ---------------------------------------------------------------------------
# Non-destructive commands
# ---------------------------------------------------------------------------


def test_approve_moves_project_to_building() -> None:
    project = Project(name="p", spec=_spec(), state=ProjectState.AWAITING_APPROVAL)
    router, project_repo, _, _ = _make_router(project=project)
    result = router.dispatch(Approve(project_id=project.id))
    assert result.ok
    snapshot = project_repo.get(project.id)
    assert snapshot is not None
    assert snapshot.state is ProjectState.BUILDING


def test_approve_refuses_wrong_state() -> None:
    project = Project(name="p", spec=_spec(), state=ProjectState.BUILDING)
    router, _, _, _ = _make_router(project=project)
    result = router.dispatch(Approve(project_id=project.id))
    assert not result.ok
    assert "awaiting_approval" in result.detail


def test_approve_refuses_unknown_project() -> None:
    router, _, _, _ = _make_router()
    result = router.dispatch(Approve(project_id=uuid4()))
    assert not result.ok
    assert "not found" in result.detail


def test_inject_note_writes_a_row() -> None:
    project = Project(name="p", spec=_spec())
    router, _, steering, _ = _make_router(project=project)
    result = router.dispatch(InjectNote(project_id=project.id, body="focus on tests"))
    assert result.ok
    assert len(steering.notes) == 1
    assert steering.notes[0]["body"] == "focus on tests"


def test_inject_note_also_posts_an_operator_decision_to_the_boardroom() -> None:
    from aidevswarm.observability import TranscriptEntry

    class _Sink:
        def __init__(self) -> None:
            self.entries: list[TranscriptEntry] = []

        def publish(self, entry: TranscriptEntry) -> None:
            self.entries.append(entry)

    project = Project(name="p", spec=_spec())
    project_repo = InMemoryProjectRepo()
    project_repo.create(project)
    sink = _Sink()
    router = CommandRouter(
        project_repo=project_repo,
        steering_repo=_FakeSteeringRepo(),
        kill_switch=InMemoryKillSwitch(),
        transcript=sink,
    )
    router.dispatch(InjectNote(project_id=project.id, body="prioritise the parser"))
    assert len(sink.entries) == 1
    assert sink.entries[0].role == "Operator"
    assert sink.entries[0].kind == "decision"
    assert sink.entries[0].text == "prioritise the parser"


def test_pause_is_recoverable_not_a_kill() -> None:
    """Pause must set the recoverable pause signal, NOT the kill switch.

    Regression: pause used to trip the per-project kill switch, which the
    tick turns into a terminal KILLED — so 'pause' silently killed the
    project. Pause must be a distinct, non-terminal signal.
    """
    project = Project(name="p", spec=_spec())
    router, _, _, kill = _make_router(project=project)

    router.dispatch(PauseProject(project_id=project.id))
    assert kill.is_paused_for(project.id)
    assert not kill.is_tripped_for(project.id)  # NOT killed

    router.dispatch(ResumeProject(project_id=project.id))
    assert not kill.is_paused_for(project.id)
    assert not kill.is_tripped_for(project.id)


def test_resume_unblocks_a_blocked_project_to_building() -> None:
    """A blocked project resumes from where it left off (-> BUILDING)."""
    project = Project(name="p", spec=_spec(), state=ProjectState.BLOCKED)
    router, project_repo, _, _ = _make_router(project=project)
    result = router.dispatch(ResumeProject(project_id=project.id))
    assert result.ok
    snapshot = project_repo.get(project.id)
    assert snapshot is not None
    assert snapshot.state is ProjectState.BUILDING


def test_resume_resets_failed_milestone_retry_count() -> None:
    """Resuming a blocked project gives its failed milestone fresh attempts;
    otherwise it would re-block on the next failure (no real progress)."""
    project = Project(name="p", spec=_spec(), state=ProjectState.BLOCKED)
    milestone_repo = InMemoryMilestoneRepo()
    [m] = milestone_repo.create_many(
        project.id,
        [
            MilestoneSpec(
                title="m",
                description="d",
                acceptance_criteria=[AcceptanceCriterion(description="x", verifier="pytest")],
            )
        ],
    )
    milestone_repo.record_attempt(m.id, success=False, commit_hash=None)
    milestone_repo.record_attempt(m.id, success=False, commit_hash=None)
    assert milestone_repo.rows[m.id].retry_count == 2

    project_repo = InMemoryProjectRepo()
    project_repo.create(project)
    router = CommandRouter(
        project_repo=project_repo,
        steering_repo=_FakeSteeringRepo(),
        kill_switch=InMemoryKillSwitch(),
        milestone_repo=milestone_repo,
    )
    result = router.dispatch(ResumeProject(project_id=project.id))
    assert result.ok
    assert milestone_repo.rows[m.id].retry_count == 0


def test_list_state_and_show_transcript_are_acknowledgements() -> None:
    router, _, _, _ = _make_router()
    list_result = router.dispatch(ListState())
    show_result = router.dispatch(ShowTranscript(project_id=uuid4()))
    assert list_result.ok
    assert show_result.ok


# ---------------------------------------------------------------------------
# Destructive commands — confirmation guard
# ---------------------------------------------------------------------------


def test_unconfirmed_abort_is_rejected() -> None:
    """Bug in either surface must not skip the [Yes][No] echo."""
    project = Project(name="p", spec=_spec())
    router, _, _, kill = _make_router(project=project)
    result = router.dispatch(AbortProject(project_id=project.id))  # confirmed=False
    assert not result.ok
    assert result.requires_confirmation is True
    assert not kill.is_tripped_for(project.id)


def test_confirmed_abort_trips_per_project_kill_switch() -> None:
    project = Project(name="p", spec=_spec())
    router, _, _, kill = _make_router(project=project)
    result = router.dispatch(AbortProject(project_id=project.id, confirmed=True))
    assert result.ok
    assert kill.is_tripped_for(project.id)


def test_confirmed_kill_switch_trips_globally() -> None:
    router, _, _, kill = _make_router()
    result = router.dispatch(KillSwitch(confirmed=True))
    assert result.ok
    assert kill.is_tripped()


def test_confirmed_rescope_writes_a_steering_note() -> None:
    project = Project(name="p", spec=_spec())
    router, _, steering, _ = _make_router(project=project)
    result = router.dispatch(Rescope(project_id=project.id, new_scope="tiny v0", confirmed=True))
    assert result.ok
    assert any("OPERATOR RESCOPE" in n["body"] for n in steering.notes)


def test_confirmed_transform_writes_a_steering_note() -> None:
    project = Project(name="p", spec=_spec())
    router, _, steering, _ = _make_router(project=project)
    result = router.dispatch(
        TransformProject(project_id=project.id, new_direction="data tool", confirmed=True)
    )
    assert result.ok
    assert any("OPERATOR TRANSFORM" in n["body"] for n in steering.notes)


def test_confirmed_drop_and_start_new_kills_the_current_project() -> None:
    project = Project(name="p", spec=_spec())
    router, _, _, kill = _make_router(project=project)
    router.dispatch(DropAndStartNew(project_id=project.id, confirmed=True))
    assert kill.is_tripped_for(project.id)


def test_confirmed_switch_to_idea_kills_current_project() -> None:
    project = Project(name="p", spec=_spec())
    router, _, _, kill = _make_router(project=project)
    new_idea = uuid4()
    result = router.dispatch(
        SwitchToIdea(current_project_id=project.id, new_idea_id=new_idea, confirmed=True)
    )
    assert result.ok
    assert kill.is_tripped_for(project.id)


def test_confirmed_reject_idea_is_a_log_only_acknowledgement() -> None:
    """No DB yet for ideas — Phase 5 keeps this best-effort."""
    router, _, _, _ = _make_router()
    result = router.dispatch(RejectIdea(idea_id=uuid4(), confirmed=True))
    assert result.ok


def test_ideate_now_calls_the_runner_and_returns_quickly() -> None:
    """The Phase-6 operator-triggered ideation fires + returns at once."""
    fired: list[int] = []
    router, _, _, _ = _make_router()
    router.ideate_runner = lambda: fired.append(1)
    result = router.dispatch(IdeateNow())
    assert result.ok
    assert fired == [1]
    assert "scheduled" in result.detail


def test_ideate_now_returns_soft_error_when_runner_raises() -> None:
    """Runner errors must NOT crash the dispatcher — soft failure instead."""

    def boom() -> None:
        raise RuntimeError("ideation crew not wired in this build")

    router, _, _, _ = _make_router()
    router.ideate_runner = boom
    result = router.dispatch(IdeateNow())
    assert result.ok is False
    assert "scheduling failed" in result.detail


def test_ideate_now_default_runner_is_a_safe_noop() -> None:
    """Without an injected runner the dispatcher still returns ok=True."""
    router, _, _, _ = _make_router()  # uses default no-op runner
    result = router.dispatch(IdeateNow())
    assert result.ok
