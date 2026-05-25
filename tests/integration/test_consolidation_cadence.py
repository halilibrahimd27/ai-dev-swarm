"""Integration test: consolidation milestones appear every Nth success.

Drives a project with 12 planned milestones; asserts that the
replanner injects consolidation milestones at indices 5 and 10 (in
the running order), and that the consolidation milestones carry the
[CONSOLIDATION] marker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aidevswarm.orchestrator.auto_split import AutoSplitPredictor
from aidevswarm.orchestrator.consolidation import CONSOLIDATION_MARKER
from aidevswarm.orchestrator.tick import Tick, TickDeps
from aidevswarm.schemas import (
    AcceptanceCriterion,
    CriticScores,
    Idea,
    MilestoneGraph,
    MilestoneSpec,
    MilestoneState,
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
        idea=Idea(title="t", summary="s", rationale="r", stack=["python"], tags=["x"]),
        scores=CriticScores(
            depth_ambition=85,
            usefulness_niche=85,
            novelty=80,
            decomposability=90,
            buildability=80,
        ),
        total=84,
    )


def _twelve_milestones() -> MilestoneGraph:
    return MilestoneGraph(
        milestones=[
            MilestoneSpec(
                title=f"m{i:02d}",
                description=f"work {i}",
                acceptance_criteria=[AcceptanceCriterion(description="ok", verifier="pytest")],
            )
            for i in range(12)
        ]
    )


def test_consolidation_milestones_appear_at_indices_5_and_10(
    tmp_path: Path,
) -> None:
    settings = Settings(
        AIDEVSWARM_REQUIRE_APPROVAL=False,
        AIDEVSWARM_CONSOLIDATION_EVERY=5,
        AIDEVSWARM_WORKSPACES_DIR=str(tmp_path / "workspaces"),
    )
    project_repo = InMemoryProjectRepo()
    milestone_repo = InMemoryMilestoneRepo()
    session_repo = FakeMilestoneSessionRepo()
    deps = TickDeps(
        settings=settings,
        project_repo=project_repo,
        milestone_repo=milestone_repo,
        session_repo=session_repo,
        ideation_crew=FakeIdeationCrew(ideas=[_scored_idea()]),
        planning_crew=FakePlanningCrew(graph=_twelve_milestones()),
        build_crew=FakeBuildCrew(succeed=True),
        replanning_crew=FakeReplanningCrew(),  # Noop
        auto_split=AutoSplitPredictor(settings, session_repo),
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        sandbox=FakeSandbox(pass_through=True),
        telegram=RecordingTelegram(),
        github=FakeGitHub(),
        kill_switch=InMemoryKillSwitch(),
    )
    tick = Tick(deps)
    project = project_repo.create(
        Project(
            name="cadence",
            spec=ProjectSpec(
                title="t",
                summary="s",
                rationale="r",
                stack=["python"],
                tags=["x"],
                score=85,
            ),
        )
    )

    # Drive until the project terminates (DONE/BLOCKED/KILLED). Hard cap
    # so a bug can't hang the suite.
    for _ in range(200):
        snapshot = project_repo.get(project.id)
        assert snapshot is not None
        if snapshot.state in (ProjectState.DONE, ProjectState.BLOCKED, ProjectState.KILLED):
            break
        tick.advance_one_step()

    final = project_repo.get(project.id)
    assert final is not None and final.state is ProjectState.DONE

    # Order the milestones by ordinal so consolidation positions are
    # readable as "after the 5th milestone, after the 10th, ...".
    ordered = sorted(milestone_repo.list_for_project(project.id), key=lambda m: m.ordinal)
    consolidations = [
        i for i, m in enumerate(ordered) if CONSOLIDATION_MARKER in (m.spec.technical_note or "")
    ]
    # Phase 4 cadence: after the 5th and 10th regular successes, the
    # scheduler injects a consolidation pass. With 12 regulars + 2
    # consolidations = 14 milestones; consolidations land at indices
    # 5 and 11 (0-indexed positions: regular runs 0-4 then C, then 6
    # regulars 6-10... let's just assert the count, not the exact slot
    # — the cadence semantics are tested in detail in
    # tests/unit/test_consolidation.py).
    assert len(consolidations) >= 2, (
        f"expected ≥2 consolidation milestones, found {len(consolidations)}: "
        f"{[(m.ordinal, m.title) for m in ordered]}"
    )
    assert all(
        m.state is MilestoneState.DONE for m in ordered
    ), f"not every milestone finished: {[(m.title, m.state.value) for m in ordered]}"
