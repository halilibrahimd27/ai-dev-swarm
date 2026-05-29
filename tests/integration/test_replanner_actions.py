"""Integration coverage for the Replanner Amend and Escalate paths.

The Noop and Split paths are exercised by other Phase 4 integration
tests (`test_concurrent_projects`, `test_auto_split`). This file
specifically drives a single project through ``REPLANNING`` with a
``FakeReplanningCrew`` configured to return ``Amend`` and ``Escalate``,
so ``Tick._apply_action`` is fully covered.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aidevswarm.orchestrator.auto_split import AutoSplitPredictor
from aidevswarm.orchestrator.tick import Tick, TickDeps
from aidevswarm.schemas import (
    AcceptanceCriterion,
    Amend,
    CriticScores,
    Escalate,
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


def _project(name: str) -> Project:
    return Project(
        name=name,
        spec=ProjectSpec(
            title=name, summary="s", rationale="r", stack=["python"], tags=["x"], score=85
        ),
    )


def _two_milestone_graph() -> MilestoneGraph:
    return MilestoneGraph(
        milestones=[
            MilestoneSpec(
                title=f"m{i}",
                description=f"d{i}",
                acceptance_criteria=[AcceptanceCriterion(description="ok", verifier="pytest")],
            )
            for i in range(2)
        ]
    )


def _build_deps(
    tmp_path: Path,
    replanning_crew: FakeReplanningCrew,
    *,
    build_succeed: bool = True,
) -> tuple[Tick, InMemoryProjectRepo, InMemoryMilestoneRepo, RecordingTelegram]:
    settings = Settings(
        AIDEVSWARM_REQUIRE_APPROVAL=False,
        AIDEVSWARM_MILESTONE_RETRY_LIMIT=2,
        AIDEVSWARM_WORKSPACES_DIR=str(tmp_path / "workspaces"),
        AIDEVSWARM_CONSOLIDATION_EVERY=999,
    )
    project_repo = InMemoryProjectRepo()
    milestone_repo = InMemoryMilestoneRepo()
    session_repo = FakeMilestoneSessionRepo()
    telegram = RecordingTelegram()
    deps = TickDeps(
        settings=settings,
        project_repo=project_repo,
        milestone_repo=milestone_repo,
        session_repo=session_repo,
        ideation_crew=FakeIdeationCrew(ideas=[_scored_idea()]),
        planning_crew=FakePlanningCrew(graph=_two_milestone_graph()),
        build_crew=FakeBuildCrew(succeed=build_succeed),
        replanning_crew=replanning_crew,
        auto_split=AutoSplitPredictor(settings, session_repo),
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        sandbox=FakeSandbox(pass_through=True),
        telegram=telegram,
        github=FakeGitHub(),
        kill_switch=InMemoryKillSwitch(),
    )
    return Tick(deps), project_repo, milestone_repo, telegram


def test_replanner_amend_patches_milestone_spec_and_returns_to_building(
    tmp_path: Path,
) -> None:
    """Amend path: patch is applied via ``MilestoneRepo.update_spec``.

    The LLM replanner only runs on a *failure* signal (the upcoming
    milestone has ``retry_count > 0``) — a clean milestone success skips
    it to save cost. So we drive a failing build first, which routes the
    just-failed milestone back through REPLANNING with retry_count == 1.
    """
    crew = FakeReplanningCrew()
    tick, project_repo, milestone_repo, _ = _build_deps(tmp_path, crew, build_succeed=False)
    project = project_repo.create(_project("amend-target"))

    # Tick 1: QUEUED -> PLANNING.
    tick.advance_one_step()
    # Tick 2: PLANNING (creates 2 milestones) -> BUILDING.
    tick.advance_one_step()

    milestones = milestone_repo.list_for_project(project.id)
    assert len(milestones) == 2
    first, _second = milestones

    # Tick 3: BUILDING -> first milestone FAILS -> REPLANNING (retry=1).
    crew.action = Amend(
        milestone_id=first.id,
        patch={"description": "rewritten by replanner"},
    )
    tick.advance_one_step()
    snapshot = project_repo.get(project.id)
    assert snapshot is not None and snapshot.state is ProjectState.REPLANNING

    # Tick 4: REPLANNING -> LLM replanner runs (retry_count > 0) ->
    # _apply_action(Amend) -> BUILDING.
    tick.advance_one_step()

    assert crew.calls, "replanner should run when the next milestone has failed"
    after = {m.id: m for m in milestone_repo.list_for_project(project.id)}
    assert after[first.id].spec.description == "rewritten by replanner"
    snapshot = project_repo.get(project.id)
    assert snapshot is not None and snapshot.state is ProjectState.BUILDING


def test_clean_milestone_success_skips_the_llm_replanner(tmp_path: Path) -> None:
    """Cost optimisation: a passing milestone must NOT invoke the replanner.

    The next pending milestone is fresh (retry_count == 0), so the
    REPLANNING state advances straight to BUILDING via the fast path —
    no two-Opus replanner call is made.
    """
    crew = FakeReplanningCrew()
    tick, project_repo, milestone_repo, _ = _build_deps(tmp_path, crew, build_succeed=True)
    project = project_repo.create(_project("clean-success"))

    # QUEUED -> PLANNING -> BUILDING.
    tick.advance_one_step()
    tick.advance_one_step()
    # BUILDING: first milestone PASSES -> REPLANNING.
    tick.advance_one_step()
    snapshot = project_repo.get(project.id)
    assert snapshot is not None and snapshot.state is ProjectState.REPLANNING
    # REPLANNING: clean path -> straight to BUILDING, replanner skipped.
    tick.advance_one_step()

    assert crew.calls == [], "replanner must be skipped on a clean milestone success"
    snapshot = project_repo.get(project.id)
    assert snapshot is not None and snapshot.state is ProjectState.BUILDING


def test_replanner_escalate_routes_to_blocked_and_pings_telegram(
    tmp_path: Path,
) -> None:
    """Escalate path: project goes BLOCKED + an alert is sent."""
    crew = FakeReplanningCrew()
    tick, project_repo, milestone_repo, telegram = _build_deps(tmp_path, crew, build_succeed=False)
    project = project_repo.create(_project("escalate-target"))

    # Drive: QUEUED -> PLANNING -> BUILDING.
    tick.advance_one_step()
    tick.advance_one_step()
    milestones = milestone_repo.list_for_project(project.id)
    assert len(milestones) == 2

    # Build first milestone FAILS -> REPLANNING (retry_count == 1).
    crew.action = Escalate(reason="developer keeps regressing tests", freeze=True)
    tick.advance_one_step()
    # REPLANNING -> LLM replanner runs -> _apply_action(Escalate) -> BLOCKED.
    tick.advance_one_step()

    snapshot = project_repo.get(project.id)
    assert snapshot is not None and snapshot.state is ProjectState.BLOCKED
    # The block reason is persisted so the UI can explain *why* it stopped.
    assert snapshot.status_detail is not None
    assert "escalated" in snapshot.status_detail.lower()
    # An operator-facing alert was sent.
    assert any("escalated" in msg.lower() for msg in telegram.sent), telegram.sent
