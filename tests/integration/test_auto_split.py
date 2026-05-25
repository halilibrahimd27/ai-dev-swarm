"""Integration test: auto-split fires when history exceeds budgets.

Drives a single project through fake crews; seeds a milestone session
record that's over both budgets; asserts the replanner replaces the
milestone with two children and that both finish.
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


def _project() -> Project:
    return Project(
        name="auto-split-target",
        spec=ProjectSpec(
            title="t", summary="s", rationale="r", stack=["python"], tags=["x"], score=85
        ),
    )


def _two_milestone_graph() -> MilestoneGraph:
    return MilestoneGraph(
        milestones=[
            MilestoneSpec(
                title="small",
                description="warmup",
                acceptance_criteria=[AcceptanceCriterion(description="x", verifier="pytest")],
            ),
            MilestoneSpec(
                title="huge",
                description="big work",
                acceptance_criteria=[
                    AcceptanceCriterion(description="a", verifier="pytest"),
                    AcceptanceCriterion(description="b", verifier="pytest"),
                    AcceptanceCriterion(description="c", verifier="pytest"),
                    AcceptanceCriterion(description="d", verifier="pytest"),
                ],
            ),
        ]
    )


def test_overbudget_history_triggers_split_and_both_children_finish(
    tmp_path: Path,
) -> None:
    settings = Settings(
        AIDEVSWARM_REQUIRE_APPROVAL=False,
        AIDEVSWARM_MILESTONE_RETRY_LIMIT=2,
        AIDEVSWARM_AUTO_SPLIT_MAX_TURNS=10,
        AIDEVSWARM_AUTO_SPLIT_MAX_COST_USD=0.5,
        AIDEVSWARM_WORKSPACES_DIR=str(tmp_path / "workspaces"),
        AIDEVSWARM_CONSOLIDATION_EVERY=999,  # disabled
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
        planning_crew=FakePlanningCrew(graph=_two_milestone_graph()),
        build_crew=FakeBuildCrew(succeed=True),
        replanning_crew=FakeReplanningCrew(),  # only reached if auto-split misses
        auto_split=AutoSplitPredictor(settings, session_repo),
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        sandbox=FakeSandbox(pass_through=True),
        telegram=RecordingTelegram(),
        github=FakeGitHub(),
        kill_switch=InMemoryKillSwitch(),
    )
    tick = Tick(deps)

    project = project_repo.create(_project())

    # Drive queued -> planning -> building. Planning creates 2 milestones.
    for _ in range(3):
        tick.advance_one_step()

    milestones = milestone_repo.list_for_project(project.id)
    assert len(milestones) == 2
    small, huge = milestones

    # Seed an over-budget session against the SECOND (still pending)
    # milestone so the next REPLANNING pass auto-splits it.
    session_repo.record(
        milestone_id=huge.id,
        role="Developer",
        session_id="too-big",
        cost_usd=99.0,
        turns=99,
    )

    # Drive until terminal — `small` finishes, REPLANNING auto-splits
    # `huge` into two children, both finish, project hits DONE.
    for _ in range(40):
        snapshot = project_repo.get(project.id)
        assert snapshot is not None
        if snapshot.state in (ProjectState.DONE, ProjectState.BLOCKED, ProjectState.KILLED):
            break
        tick.advance_one_step()

    after = milestone_repo.list_for_project(project.id)
    assert huge.id not in [
        m.id for m in after
    ], "the over-budget milestone should have been auto-split away"
    # We expect at least: small + 2 children of huge.
    assert len(after) >= 3, f"expected ≥3 milestones after split, got {len(after)}"
    assert all(
        m.state is MilestoneState.DONE for m in after
    ), f"not every milestone finished: {[(m.title, m.state.value) for m in after]}"
    final = project_repo.get(project.id)
    assert final is not None and final.state is ProjectState.DONE
