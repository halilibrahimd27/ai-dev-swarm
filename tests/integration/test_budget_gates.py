"""Integration coverage for the tick's token-budget gates.

Two behaviours, both new:
  * Daily throttle -> the tick PAUSES (returns None, no state change, no
    build) so the project resumes once the UTC day rolls over.
  * Per-milestone sanity cap -> the milestone is counted as a failed
    attempt and routed to REPLANNING (or BLOCKED past the retry limit),
    so a runaway milestone stops burning tokens.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from aidevswarm.orchestrator.auto_split import AutoSplitPredictor
from aidevswarm.orchestrator.tick import Tick, TickDeps
from aidevswarm.schemas import (
    AcceptanceCriterion,
    MilestoneGraph,
    MilestoneSpec,
    MilestoneState,
    Project,
    ProjectSpec,
    ProjectState,
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


class _FakeBudget:
    """Configurable TokenBudget: daily / per-milestone gates toggle freely."""

    def __init__(self, *, daily_ok: bool = True, milestone_ok: bool = True) -> None:
        self.daily_ok = daily_ok
        self.milestone_ok = milestone_ok

    def can_spend(self, *, milestone_id: UUID | None, requested: int) -> bool:
        return self.daily_ok if milestone_id is None else self.milestone_ok

    def record_spend(self, **_: object) -> None:
        return None


def _graph() -> MilestoneGraph:
    return MilestoneGraph(
        milestones=[
            MilestoneSpec(
                title="m0",
                description="d0",
                acceptance_criteria=[AcceptanceCriterion(description="ok", verifier="pytest")],
            )
        ]
    )


def _deps(tmp_path: Path, budget: _FakeBudget) -> tuple[Tick, InMemoryProjectRepo, FakeBuildCrew]:
    settings = Settings(
        AIDEVSWARM_REQUIRE_APPROVAL=False,
        AIDEVSWARM_MILESTONE_RETRY_LIMIT=2,
        AIDEVSWARM_WORKSPACES_DIR=str(tmp_path / "workspaces"),
        AIDEVSWARM_CONSOLIDATION_EVERY=999,
    )
    project_repo = InMemoryProjectRepo()
    build_crew = FakeBuildCrew(succeed=True)
    deps = TickDeps(
        settings=settings,
        project_repo=project_repo,
        milestone_repo=InMemoryMilestoneRepo(),
        session_repo=FakeMilestoneSessionRepo(),
        ideation_crew=FakeIdeationCrew(),
        planning_crew=FakePlanningCrew(graph=_graph()),
        build_crew=build_crew,
        replanning_crew=FakeReplanningCrew(),
        auto_split=AutoSplitPredictor(settings, FakeMilestoneSessionRepo()),
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        sandbox=FakeSandbox(pass_through=True),
        telegram=RecordingTelegram(),
        github=FakeGitHub(),
        kill_switch=InMemoryKillSwitch(),
        token_budget=budget,
    )
    return Tick(deps), project_repo, build_crew


def _drive_to_building(tick: Tick, project_repo: InMemoryProjectRepo) -> Project:
    project = project_repo.create(
        Project(
            name="p",
            spec=ProjectSpec(
                title="t", summary="s", rationale="r", stack=["python"], tags=["x"], score=85
            ),
        )
    )
    tick.advance_one_step()  # QUEUED -> PLANNING
    tick.advance_one_step()  # PLANNING -> BUILDING
    snap = project_repo.get(project.id)
    assert snap is not None and snap.state is ProjectState.BUILDING
    return project


def test_daily_budget_pauses_without_changing_state(tmp_path: Path) -> None:
    tick, project_repo, build_crew = _deps(tmp_path, _FakeBudget(daily_ok=False))
    project = _drive_to_building(tick, project_repo)

    result = tick.advance_one_step()  # BUILDING, but daily budget exhausted
    assert result is None  # paused
    snap = project_repo.get(project.id)
    assert snap is not None and snap.state is ProjectState.BUILDING  # unchanged
    assert build_crew.calls == 0  # no LLM work happened


def test_per_milestone_cap_routes_to_replanning(tmp_path: Path) -> None:
    tick, project_repo, build_crew = _deps(tmp_path, _FakeBudget(daily_ok=True, milestone_ok=False))
    project = _drive_to_building(tick, project_repo)

    tick.advance_one_step()  # BUILDING -> milestone over cap -> REPLANNING
    snap = project_repo.get(project.id)
    assert snap is not None and snap.state is ProjectState.REPLANNING
    assert build_crew.calls == 0  # the runaway milestone was NOT rebuilt
    # The milestone was counted as a failed attempt.
    milestones = tick._d.milestone_repo.list_for_project(project.id)
    assert milestones[0].state is MilestoneState.FAILED
    assert milestones[0].retry_count == 1
