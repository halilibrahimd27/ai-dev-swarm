"""Phase 4 integration test: scheduler pool with 3 concurrent projects.

Drives 3 toy projects through the in-memory fake stack; one is killed
on purpose; asserts the other two finish without the third's failure
holding them back. No live LLM, no Postgres — everything in-memory.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import pytest

from aidevswarm.orchestrator.auto_split import AutoSplitPredictor
from aidevswarm.orchestrator.scheduler import ProjectPool
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


def _graph() -> MilestoneGraph:
    return MilestoneGraph(
        milestones=[
            MilestoneSpec(
                title=f"m{i}",
                description=f"do thing {i}",
                acceptance_criteria=[AcceptanceCriterion(description="ok", verifier="pytest")],
            )
            for i in range(2)
        ]
    )


def _build_tick(tmp_path: Path) -> tuple[Tick, dict[str, object]]:
    settings = Settings(
        AIDEVSWARM_REQUIRE_APPROVAL=False,
        AIDEVSWARM_MILESTONE_RETRY_LIMIT=2,
        AIDEVSWARM_BUILD_CONCURRENCY=3,
        AIDEVSWARM_WORKSPACES_DIR=str(tmp_path / "workspaces"),
        AIDEVSWARM_CONSOLIDATION_EVERY=999,  # disabled for this test
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
        planning_crew=FakePlanningCrew(graph=_graph()),
        build_crew=FakeBuildCrew(succeed=True),
        replanning_crew=FakeReplanningCrew(),  # default Noop
        auto_split=AutoSplitPredictor(settings, session_repo),
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        sandbox=FakeSandbox(pass_through=True),
        telegram=RecordingTelegram(),
        github=FakeGitHub(),
        kill_switch=InMemoryKillSwitch(),
    )
    tick = Tick(deps)
    return tick, {
        "project_repo": project_repo,
        "kill_switch": deps.kill_switch,
        "settings": settings,
    }


@pytest.mark.asyncio
async def test_three_concurrent_projects_two_finish_one_killed(
    tmp_path: Path,
) -> None:
    """Spin up 3 projects, kill one mid-flight, the other two reach DONE."""
    tick, state = _build_tick(tmp_path)
    project_repo: InMemoryProjectRepo = state["project_repo"]  # type: ignore[assignment]
    kill: InMemoryKillSwitch = state["kill_switch"]  # type: ignore[assignment]

    # Create 3 projects with distinct created_at timestamps so the
    # fair-share order is deterministic.
    project_a = project_repo.create(_project("alpha"))
    project_b = project_repo.create(_project("bravo"))
    project_c = project_repo.create(_project("charlie"))

    # Pre-kill project_c. The pool should mark it KILLED on first claim.
    kill.trip_for(project_c.id, reason="test")

    pool = ProjectPool(
        tick=tick,
        project_repo=project_repo,
        concurrency=3,
        poll_seconds=0.001,
    )

    # Run the pool for up to ~3 seconds; abort the moment all three
    # projects are terminal.
    async def watcher() -> None:
        for _ in range(300):
            await asyncio.sleep(0.01)
            states = {p.id: p.state for p in project_repo.rows.values()}
            if all(
                s in {ProjectState.DONE, ProjectState.KILLED, ProjectState.BLOCKED}
                for s in states.values()
            ):
                return
        pytest.fail("not all projects reached terminal within budget")

    pool_task = asyncio.create_task(pool.run_forever())
    try:
        await asyncio.wait_for(watcher(), timeout=5.0)
    finally:
        pool_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pool_task

    final_a = project_repo.get(project_a.id)
    final_b = project_repo.get(project_b.id)
    final_c = project_repo.get(project_c.id)
    assert final_a is not None and final_a.state is ProjectState.DONE
    assert final_b is not None and final_b.state is ProjectState.DONE
    assert final_c is not None and final_c.state is ProjectState.KILLED


@pytest.mark.asyncio
async def test_pool_rejects_zero_concurrency(tmp_path: Path) -> None:
    tick, _ = _build_tick(tmp_path)
    with pytest.raises(ValueError):
        ProjectPool(tick=tick, project_repo=tick._d.project_repo, concurrency=0)


@pytest.mark.asyncio
async def test_pool_drain_once_advances_idle_returns_zero(tmp_path: Path) -> None:
    tick, state = _build_tick(tmp_path)
    project_repo = state["project_repo"]
    pool = ProjectPool(
        tick=tick,
        project_repo=project_repo,  # type: ignore[arg-type]
        concurrency=2,
        poll_seconds=0.001,
    )
    # Nothing in the queue -> drain_once should advance 0 projects.
    n = await pool.drain_once()
    assert n == 0
