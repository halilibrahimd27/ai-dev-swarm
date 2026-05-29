"""End-to-end smoke test for the Phase 0 orchestrator.

Drives a single project through:

    queued -> planning -> awaiting_approval -> building -> integration -> done

using in-memory fakes for every external dependency (DB, Redis, Docker,
GitHub, Telegram, LLM). Verifies:

  * Each transition is legal (the state machine guard would raise
    otherwise).
  * Every milestone produces a commit in the persistent workspace.
  * The Telegram notifier observes the approval ping and the publish.

No Postgres, no Redis, no Docker, no real LLM.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aidevswarm.orchestrator.auto_split import AutoSplitPredictor
from aidevswarm.orchestrator.tick import Tick, TickDeps
from aidevswarm.schemas import (
    AcceptanceCriterion,
    CriticScores,
    Idea,
    MilestoneGraph,
    MilestoneSpec,
    Project,
    ProjectSpec,
    ProjectState,
    ScoredIdea,
)
from aidevswarm.settings import Settings
from aidevswarm.tools.kill_switch import InMemoryKillSwitch
from aidevswarm.tools.workspace import WorkspaceManager
from tests.fakes import (
    FakeBuildCrew,
    FakeGitHub,
    FakeIdeationCrew,
    FakeMilestoneSessionRepo,
    FakePlanningCrew,
    FakeReplanningCrew,
    FakeSandbox,
    InMemoryMilestoneRepo,
    InMemoryProjectRepo,
    RecordingTelegram,
)

pytestmark = pytest.mark.integration


def _scored_idea() -> ScoredIdea:
    return ScoredIdea(
        idea=Idea(
            title="local prom mirror",
            summary="self-host a niche tool",
            rationale="serves a small but real audience",
            stack=["python"],
            tags=["niche", "phase-0"],
        ),
        scores=CriticScores(
            depth_ambition=85,
            usefulness_niche=85,
            novelty=80,
            decomposability=90,
            buildability=80,
        ),
        total=84,
    )


def _project() -> Project:
    spec = ProjectSpec(
        title="phase-0-smoke",
        summary="end-to-end smoke target",
        rationale="exercise the state machine",
        stack=["python"],
        tags=["smoke"],
        score=84,
    )
    return Project(name="phase-0-smoke", spec=spec)


def _milestone_graph() -> MilestoneGraph:
    return MilestoneGraph(
        milestones=[
            MilestoneSpec(
                title="bootstrap",
                description="scaffold pyproject + Makefile",
                acceptance_criteria=[
                    AcceptanceCriterion(description="ruff exits 0", verifier="lint")
                ],
                technical_note="use uv",
            ),
            MilestoneSpec(
                title="core feature",
                description="implement the headline behaviour",
                acceptance_criteria=[
                    AcceptanceCriterion(description="pytest passes", verifier="pytest")
                ],
                technical_note="prefer stdlib",
            ),
        ]
    )


def _build_tick(tmp_path: Path, *, require_approval: bool = False) -> tuple[Tick, dict]:
    settings = Settings(
        AIDEVSWARM_REQUIRE_APPROVAL=require_approval,
        AIDEVSWARM_MILESTONE_RETRY_LIMIT=2,
        AIDEVSWARM_WORKSPACES_DIR=str(tmp_path / "workspaces"),
    )
    project_repo = InMemoryProjectRepo()
    milestone_repo = InMemoryMilestoneRepo()
    session_repo = FakeMilestoneSessionRepo()
    ideation = FakeIdeationCrew(ideas=[_scored_idea()])
    planning = FakePlanningCrew(graph=_milestone_graph())
    build = FakeBuildCrew(succeed=True)
    replanner = FakeReplanningCrew()  # default Noop
    auto_split = AutoSplitPredictor(settings, session_repo)
    workspace_manager = WorkspaceManager(tmp_path / "workspaces")
    sandbox = FakeSandbox(pass_through=True)
    telegram = RecordingTelegram()
    github = FakeGitHub()
    kill = InMemoryKillSwitch()

    deps = TickDeps(
        settings=settings,
        project_repo=project_repo,
        milestone_repo=milestone_repo,
        session_repo=session_repo,
        ideation_crew=ideation,
        planning_crew=planning,
        build_crew=build,
        replanning_crew=replanner,
        auto_split=auto_split,
        workspace_manager=workspace_manager,
        sandbox=sandbox,
        telegram=telegram,
        github=github,
        kill_switch=kill,
    )
    tick = Tick(deps)
    return tick, {
        "project_repo": project_repo,
        "milestone_repo": milestone_repo,
        "build": build,
        "telegram": telegram,
        "github": github,
        "workspace_manager": workspace_manager,
        "kill_switch": kill,
    }


def _run_until_terminal(tick: Tick, max_steps: int = 30) -> int:
    """Tick until the active project reaches a terminal state."""
    for n in range(max_steps):
        result = tick.advance_one_step()
        if result is None:
            # Nothing happened — but only after the project is terminal,
            # because the tick returns None on idle too. Bail if we ran
            # out of work to do.
            return n
        if result.state in {ProjectState.DONE, ProjectState.KILLED}:
            return n + 1
    raise AssertionError("orchestrator did not terminate within max_steps")


def test_queued_to_done_happy_path(tmp_path: Path) -> None:
    tick, state = _build_tick(tmp_path, require_approval=False)
    project_repo: InMemoryProjectRepo = state["project_repo"]
    milestone_repo: InMemoryMilestoneRepo = state["milestone_repo"]
    build: FakeBuildCrew = state["build"]
    telegram: RecordingTelegram = state["telegram"]

    project = project_repo.create(_project())
    assert project.state is ProjectState.QUEUED

    _run_until_terminal(tick)

    stored = project_repo.get(project.id)
    assert stored is not None
    assert stored.state is ProjectState.DONE
    # Two milestones planned and both built once each.
    assert build.calls == 2
    milestones = milestone_repo.list_for_project(project.id)
    assert len(milestones) == 2
    assert all(ms.state.value == "done" for ms in milestones)

    # Each milestone added at least one commit to the workspace.
    ws = state["workspace_manager"].for_project(project.name)
    assert ws.commit_count() >= 1 + len(milestones)
    # No approval ping when require_approval is false.
    assert all("awaits plan approval" not in m for m in telegram.sent)


def test_awaits_approval_when_configured(tmp_path: Path) -> None:
    tick, state = _build_tick(tmp_path, require_approval=True)
    project_repo: InMemoryProjectRepo = state["project_repo"]
    telegram: RecordingTelegram = state["telegram"]

    project = project_repo.create(_project())
    # queued -> planning
    tick.advance_one_step()
    # planning -> awaiting_approval (one transition per tick)
    tick.advance_one_step()
    stored = project_repo.get(project.id)
    assert stored is not None
    assert stored.state is ProjectState.AWAITING_APPROVAL
    assert any("awaits plan approval" in m for m in telegram.sent)
    # Further ticks should be no-ops until an external approval flips
    # the state.
    before = stored.updated_at
    tick.advance_one_step()
    after = project_repo.get(project.id)
    assert after is not None
    assert after.updated_at == before


def test_kill_switch_halts_advance(tmp_path: Path) -> None:
    tick, state = _build_tick(tmp_path, require_approval=False)
    project_repo: InMemoryProjectRepo = state["project_repo"]
    state["kill_switch"].trip("test")

    project_repo.create(_project())
    assert tick.advance_one_step() is None
    queued = project_repo.list_by_state(ProjectState.QUEUED)
    assert len(queued) == 1
    assert queued[0].state is ProjectState.QUEUED


def test_paused_project_is_skipped_not_killed(tmp_path: Path) -> None:
    """Regression: pause must skip the tick, NOT make the project terminal.

    A per-project kill -> KILLED; a per-project PAUSE -> the tick returns
    None and the project keeps its state so resume can continue it.
    """
    tick, state = _build_tick(tmp_path, require_approval=False)
    project_repo: InMemoryProjectRepo = state["project_repo"]
    project = project_repo.create(_project())
    project = project_repo.update_state(project.id, ProjectState.PLANNING)

    state["kill_switch"].pause_for(project.id)
    assert tick.advance_project(project) is None
    stored = project_repo.get(project.id)
    assert stored is not None
    assert stored.state is ProjectState.PLANNING  # unchanged — NOT killed

    # Unpause -> the tick advances it again.
    state["kill_switch"].unpause_for(project.id)
    assert tick.advance_project(project) is not None


def test_milestone_failure_blocks_after_retry_limit(tmp_path: Path) -> None:
    tick, state = _build_tick(tmp_path, require_approval=False)
    state["build"].succeed = False  # every build fails
    project_repo: InMemoryProjectRepo = state["project_repo"]

    project = project_repo.create(_project())
    _run_until_terminal(tick, max_steps=60)

    stored = project_repo.get(project.id)
    assert stored is not None
    assert stored.state is ProjectState.BLOCKED
    telegram: RecordingTelegram = state["telegram"]
    assert any("blocked on milestone" in m for m in telegram.sent)
