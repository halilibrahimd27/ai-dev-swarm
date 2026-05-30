"""Drift/scope guardrail: a sprawling project is blocked for review."""

from __future__ import annotations

from pathlib import Path

import pytest

from aidevswarm.orchestrator.auto_split import AutoSplitPredictor
from aidevswarm.orchestrator.tick import Tick, TickDeps
from aidevswarm.schemas import (
    AcceptanceCriterion,
    MilestoneSpec,
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


def test_milestone_scope_cap_blocks_for_review(tmp_path: Path) -> None:
    settings = Settings(
        AIDEVSWARM_REQUIRE_APPROVAL=False,
        AIDEVSWARM_WORKSPACES_DIR=str(tmp_path / "workspaces"),
        AIDEVSWARM_MAX_PROJECT_MILESTONES=2,  # tiny cap for the test
        AIDEVSWARM_CONSOLIDATION_EVERY=999,
    )
    project_repo = InMemoryProjectRepo()
    milestone_repo = InMemoryMilestoneRepo()
    telegram = RecordingTelegram()
    deps = TickDeps(
        settings=settings,
        project_repo=project_repo,
        milestone_repo=milestone_repo,
        session_repo=FakeMilestoneSessionRepo(),
        ideation_crew=FakeIdeationCrew(ideas=[]),
        planning_crew=FakePlanningCrew(graph=None),  # type: ignore[arg-type]
        build_crew=FakeBuildCrew(),
        replanning_crew=FakeReplanningCrew(),
        auto_split=AutoSplitPredictor(settings, FakeMilestoneSessionRepo()),
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        sandbox=FakeSandbox(pass_through=True),
        telegram=telegram,
        github=FakeGitHub(),
        kill_switch=InMemoryKillSwitch(),
    )
    tick = Tick(deps)
    project = project_repo.create(
        Project(
            name="sprawler",
            state=ProjectState.BUILDING,
            spec=ProjectSpec(
                title="t", summary="s", rationale="r", stack=["python"], tags=["x"], score=85
            ),
        )
    )
    milestone_repo.create_many(
        project.id,
        [
            MilestoneSpec(
                title=f"m{i}",
                description="d",
                acceptance_criteria=[AcceptanceCriterion(description="ok", verifier="pytest")],
            )
            for i in range(3)  # 3 > cap of 2
        ],
    )

    tick.advance_project(project)

    snap = project_repo.get(project.id)
    assert snap.state is ProjectState.BLOCKED
    assert "scope guardrail" in (snap.status_detail or "")
    assert any("scope cap" in m for m in telegram.sent)
