"""The integration stage runs a final whole-repo CI gate before DONE.

Per-milestone CI runs piecemeal; integration runs the full repo once more
so a project can't be declared shipped if the finished whole doesn't pass.
Green → DONE; red → BLOCKED (not silently shipped).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aidevswarm.orchestrator.auto_split import AutoSplitPredictor
from aidevswarm.orchestrator.tick import Tick, TickDeps
from aidevswarm.schemas import Project, ProjectSpec, ProjectState
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


def _tick(
    tmp_path: Path, *, ci_passes: bool
) -> tuple[Tick, InMemoryProjectRepo, RecordingTelegram]:
    settings = Settings(AIDEVSWARM_WORKSPACES_DIR=str(tmp_path / "workspaces"))
    project_repo = InMemoryProjectRepo()
    telegram = RecordingTelegram()
    deps = TickDeps(
        settings=settings,
        project_repo=project_repo,
        milestone_repo=InMemoryMilestoneRepo(),
        session_repo=FakeMilestoneSessionRepo(),
        ideation_crew=FakeIdeationCrew(ideas=[]),
        planning_crew=FakePlanningCrew(graph=None),  # type: ignore[arg-type]
        build_crew=FakeBuildCrew(),
        replanning_crew=FakeReplanningCrew(),
        auto_split=AutoSplitPredictor(settings, FakeMilestoneSessionRepo()),
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        sandbox=FakeSandbox(pass_through=ci_passes),
        telegram=telegram,
        github=FakeGitHub(),
        kill_switch=InMemoryKillSwitch(),
    )
    return Tick(deps), project_repo, telegram


def _project() -> Project:
    return Project(
        name="ship-me",
        state=ProjectState.INTEGRATION,
        spec=ProjectSpec(
            title="t", summary="s", rationale="r", stack=["python"], tags=["x"], score=85
        ),
    )


def test_integration_ships_on_green_full_repo_ci(tmp_path: Path) -> None:
    tick, project_repo, telegram = _tick(tmp_path, ci_passes=True)
    project = project_repo.create(_project())
    tick._integrate(project)
    assert project_repo.get(project.id).state is ProjectState.DONE


def test_integration_blocks_on_red_full_repo_ci(tmp_path: Path) -> None:
    tick, project_repo, telegram = _tick(tmp_path, ci_passes=False)
    project = project_repo.create(_project())
    tick._integrate(project)
    snap = project_repo.get(project.id)
    assert snap.state is ProjectState.BLOCKED
    assert "integration CI failed" in (snap.status_detail or "")
    assert any("integration CI" in m for m in telegram.sent)
