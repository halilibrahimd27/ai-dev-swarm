"""The tick invokes the Diagnostician on a milestone quality-failure.

Proves the self-healing wiring: when a build fails, the tick hands the
concrete failure to the Diagnostician (which steers the next attempt)
before routing to the replanner.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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

pytestmark = pytest.mark.integration


class _SpyDiagnostician:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def diagnose(self, project: Any, milestone: Any, failure_reason: str) -> str | None:
        self.calls.append((milestone.title, failure_reason))
        return "fix it like so"


def _graph() -> MilestoneGraph:
    return MilestoneGraph(
        milestones=[
            MilestoneSpec(
                title="m0",
                description="d",
                acceptance_criteria=[AcceptanceCriterion(description="ok", verifier="pytest")],
            )
        ]
    )


def test_diagnostician_runs_on_milestone_failure(tmp_path: Path) -> None:
    settings = Settings(
        AIDEVSWARM_REQUIRE_APPROVAL=False,
        AIDEVSWARM_MILESTONE_RETRY_LIMIT=2,
        AIDEVSWARM_WORKSPACES_DIR=str(tmp_path / "workspaces"),
        AIDEVSWARM_CONSOLIDATION_EVERY=999,
    )
    project_repo = InMemoryProjectRepo()
    milestone_repo = InMemoryMilestoneRepo()
    session_repo = FakeMilestoneSessionRepo()
    spy = _SpyDiagnostician()
    deps = TickDeps(
        settings=settings,
        project_repo=project_repo,
        milestone_repo=milestone_repo,
        session_repo=session_repo,
        ideation_crew=FakeIdeationCrew(ideas=[]),
        planning_crew=FakePlanningCrew(graph=_graph()),
        build_crew=FakeBuildCrew(succeed=False),  # force a quality failure
        replanning_crew=FakeReplanningCrew(),
        auto_split=AutoSplitPredictor(settings, session_repo),
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        sandbox=FakeSandbox(pass_through=True),
        telegram=RecordingTelegram(),
        github=FakeGitHub(),
        kill_switch=InMemoryKillSwitch(),
        diagnostician=spy,  # type: ignore[arg-type]
    )
    tick = Tick(deps)
    project = project_repo.create(
        Project(
            name="diag-target",
            spec=ProjectSpec(
                title="t", summary="s", rationale="r", stack=["python"], tags=["x"], score=85
            ),
        )
    )
    tick.advance_one_step()  # QUEUED -> PLANNING
    tick.advance_one_step()  # PLANNING -> BUILDING
    tick.advance_one_step()  # BUILDING: build fails -> Diagnostician runs

    assert len(spy.calls) == 1
    title, reason = spy.calls[0]
    assert title == "m0"
    assert reason == "fake"  # FakeBuildCrew's failure_reason
    del project
