"""Integration coverage for the GitHub publish chain.

Drives a project from QUEUED through its first milestone and asserts:
  * a private repo is "created" (FakeGitHub records it),
  * the project's ``github_repo`` is persisted,
  * the milestone commit is pushed to a real local *bare* repo that
    stands in for GitHub (so the push path is exercised end-to-end with
    no network).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from aidevswarm.orchestrator.auto_split import AutoSplitPredictor
from aidevswarm.orchestrator.tick import Tick, TickDeps
from aidevswarm.schemas import (
    AcceptanceCriterion,
    MilestoneGraph,
    MilestoneSpec,
    Project,
    ProjectSpec,
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

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")


def _one_milestone_graph() -> MilestoneGraph:
    return MilestoneGraph(
        milestones=[
            MilestoneSpec(
                title="m0",
                description="d0",
                acceptance_criteria=[AcceptanceCriterion(description="ok", verifier="pytest")],
            )
        ]
    )


def test_first_milestone_creates_repo_and_pushes(tmp_path: Path) -> None:
    bare = tmp_path / "remote.git"
    subprocess.check_call(["git", "init", "--bare", "-b", "main", str(bare)])

    settings = Settings(
        GITHUB_TOKEN="ghp_token",  # type: ignore[arg-type]
        AIDEVSWARM_REQUIRE_APPROVAL=False,
        AIDEVSWARM_WORKSPACES_DIR=str(tmp_path / "workspaces"),
        AIDEVSWARM_CONSOLIDATION_EVERY=999,
    )
    project_repo = InMemoryProjectRepo()
    milestone_repo = InMemoryMilestoneRepo()
    github = FakeGitHub(push_remote=f"file://{bare}")
    deps = TickDeps(
        settings=settings,
        project_repo=project_repo,
        milestone_repo=milestone_repo,
        session_repo=FakeMilestoneSessionRepo(),
        ideation_crew=FakeIdeationCrew(),
        planning_crew=FakePlanningCrew(graph=_one_milestone_graph()),
        build_crew=FakeBuildCrew(succeed=True),
        replanning_crew=FakeReplanningCrew(),
        auto_split=AutoSplitPredictor(settings, FakeMilestoneSessionRepo()),
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        sandbox=FakeSandbox(pass_through=True),
        telegram=RecordingTelegram(),
        github=github,
        kill_switch=InMemoryKillSwitch(),
    )
    tick = Tick(deps)
    project = project_repo.create(
        Project(
            name="cool-project",
            spec=ProjectSpec(
                title="cool", summary="s", rationale="r", stack=["python"], tags=["x"], score=85
            ),
        )
    )

    tick.advance_one_step()  # QUEUED -> PLANNING
    tick.advance_one_step()  # PLANNING -> BUILDING
    tick.advance_one_step()  # BUILDING -> build m0, create repo, push -> REPLANNING

    # Repo was created and persisted.
    assert github.created == ["cool-project"]
    snapshot = project_repo.get(project.id)
    assert (
        snapshot is not None and snapshot.github_repo == "https://example.invalid/fake/cool-project"
    )

    # The milestone commit reached the bare "remote".
    log = subprocess.check_output(["git", "log", "--oneline"], cwd=bare, text=True)
    assert "m0" in log


def test_no_token_means_no_repo_and_no_push(tmp_path: Path) -> None:
    """Without a GITHUB_TOKEN the build still runs locally; nothing is published."""
    settings = Settings(
        AIDEVSWARM_REQUIRE_APPROVAL=False,
        AIDEVSWARM_WORKSPACES_DIR=str(tmp_path / "workspaces"),
        AIDEVSWARM_CONSOLIDATION_EVERY=999,
    )
    project_repo = InMemoryProjectRepo()
    milestone_repo = InMemoryMilestoneRepo()
    github = FakeGitHub()
    deps = TickDeps(
        settings=settings,
        project_repo=project_repo,
        milestone_repo=milestone_repo,
        session_repo=FakeMilestoneSessionRepo(),
        ideation_crew=FakeIdeationCrew(),
        planning_crew=FakePlanningCrew(graph=_one_milestone_graph()),
        build_crew=FakeBuildCrew(succeed=True),
        replanning_crew=FakeReplanningCrew(),
        auto_split=AutoSplitPredictor(settings, FakeMilestoneSessionRepo()),
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        sandbox=FakeSandbox(pass_through=True),
        telegram=RecordingTelegram(),
        github=github,
        kill_switch=InMemoryKillSwitch(),
    )
    tick = Tick(deps)
    project = project_repo.create(
        Project(
            name="local-only",
            spec=ProjectSpec(
                title="x", summary="s", rationale="r", stack=["python"], tags=["x"], score=85
            ),
        )
    )
    tick.advance_one_step()
    tick.advance_one_step()
    tick.advance_one_step()

    assert github.created == []
    snapshot = project_repo.get(project.id)
    assert snapshot is not None and snapshot.github_repo is None
