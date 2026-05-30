"""A milestone orphaned in BUILDING is reclaimed, not skipped.

Regression for the bug where a transient-error backoff (or a mid-build
restart) left a milestone in BUILDING; next_pending then skipped it and
built a LATER milestone, leaving a silent hole. The tick now requeues
orphaned BUILDING milestones at the top of each build step, so the SAME
milestone is retried.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aidevswarm.orchestrator.auto_split import AutoSplitPredictor
from aidevswarm.orchestrator.tick import Tick, TickDeps
from aidevswarm.schemas import (
    AcceptanceCriterion,
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


def _spec(t: str) -> MilestoneSpec:
    return MilestoneSpec(
        title=t,
        description="d",
        acceptance_criteria=[AcceptanceCriterion(description="ok", verifier="pytest")],
    )


def test_orphaned_building_milestone_is_reclaimed_not_skipped(tmp_path: Path) -> None:
    settings = Settings(
        AIDEVSWARM_REQUIRE_APPROVAL=False,
        AIDEVSWARM_WORKSPACES_DIR=str(tmp_path / "workspaces"),
        AIDEVSWARM_CONSOLIDATION_EVERY=999,
    )
    project_repo = InMemoryProjectRepo()
    milestone_repo = InMemoryMilestoneRepo()
    deps = TickDeps(
        settings=settings,
        project_repo=project_repo,
        milestone_repo=milestone_repo,
        session_repo=FakeMilestoneSessionRepo(),
        ideation_crew=FakeIdeationCrew(ideas=[]),
        planning_crew=FakePlanningCrew(graph=None),  # type: ignore[arg-type]
        build_crew=FakeBuildCrew(succeed=True),
        replanning_crew=FakeReplanningCrew(),
        auto_split=AutoSplitPredictor(settings, FakeMilestoneSessionRepo()),
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        sandbox=FakeSandbox(pass_through=True),
        telegram=RecordingTelegram(),
        github=FakeGitHub(),
        kill_switch=InMemoryKillSwitch(),
    )
    tick = Tick(deps)
    project = project_repo.create(
        Project(
            name="reclaim",
            state=ProjectState.BUILDING,
            spec=ProjectSpec(
                title="t", summary="s", rationale="r", stack=["python"], tags=["x"], score=85
            ),
        )
    )
    [a, b] = milestone_repo.create_many(project.id, [_spec("orphaned"), _spec("later")])
    # Simulate the orphan: 'orphaned' (ord 0) stuck BUILDING, never completed.
    milestone_repo.update_state(a.id, MilestoneState.BUILDING)

    tick.advance_project(project)

    # The orphan was reclaimed + built (DONE), NOT skipped in favour of 'later'.
    assert milestone_repo.rows[a.id].state is MilestoneState.DONE
    assert milestone_repo.rows[b.id].state is MilestoneState.PENDING
