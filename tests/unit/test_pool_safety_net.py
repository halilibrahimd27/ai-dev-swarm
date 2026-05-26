"""Phase-6 hotfix regression test for the ProjectPool safety net.

The first live `docker compose up -d` against real API keys hit a
crash loop: the planning crew's parser raised on malformed CrewAI
JSON, the exception bubbled all the way to ``asyncio.gather``,
``asyncio.gather`` cancelled every coroutine, and the orchestrator
container restarted. After restart the project was still in
``planning``, so the crew kicked off again and crashed again.

The fix is in ``ProjectPool._drain_one``: catch any exception from
``Tick.advance_project``, log it, and move the project to
``BLOCKED`` so the pool stops picking it up. This test pins the
behaviour so it never silently regresses.
"""

from __future__ import annotations

from typing import Any

import pytest

from aidevswarm.orchestrator.scheduler import ProjectPool
from aidevswarm.schemas import Project, ProjectSpec, ProjectState
from tests.fakes import InMemoryProjectRepo


class _ExplodingTick:
    """Tick that always raises on advance_project."""

    def __init__(self, project_repo: InMemoryProjectRepo) -> None:
        # The real Tick exposes `_d.project_repo` — mimic just enough
        # for the pool's `_claim_next` queries.
        self._d = type("Deps", (), {"project_repo": project_repo})()

    def advance_project(self, project: Any) -> Any:
        raise RuntimeError("simulated crew crash (bad LLM JSON)")


def _project(name: str = "victim") -> Project:
    return Project(
        name=name,
        state=ProjectState.PLANNING,
        spec=ProjectSpec(
            title=name, summary="s", rationale="r", stack=["python"], tags=["x"], score=85
        ),
    )


@pytest.mark.asyncio
async def test_advance_failure_moves_project_to_blocked() -> None:
    repo = InMemoryProjectRepo()
    project = repo.create(_project())
    pool = ProjectPool(
        tick=_ExplodingTick(repo),  # type: ignore[arg-type]
        project_repo=repo,
        concurrency=1,
        poll_seconds=0.001,
    )

    # drain_once tries to advance the project; the safety net must
    # catch the RuntimeError, mark the project BLOCKED, and return
    # without re-raising.
    advanced = await pool.drain_once()
    assert advanced >= 1
    refetched = repo.get(project.id)
    assert refetched is not None
    assert refetched.state is ProjectState.BLOCKED


@pytest.mark.asyncio
async def test_advance_failure_does_not_crash_subsequent_drains() -> None:
    """A bad project must not poison the next drain pass."""
    repo = InMemoryProjectRepo()
    repo.create(_project("p1"))
    repo.create(_project("p2"))
    pool = ProjectPool(
        tick=_ExplodingTick(repo),  # type: ignore[arg-type]
        project_repo=repo,
        concurrency=1,
        poll_seconds=0.001,
    )

    # First drain: blocks p1 (or p2 — whichever is claimed first).
    await pool.drain_once()
    # Second drain: blocks the other one.
    await pool.drain_once()

    states = {p.state for p in repo.rows.values()}
    assert states == {ProjectState.BLOCKED}


def test_planning_parse_skips_malformed_entries() -> None:
    """Planning crew parser tolerates bad milestones; empty list ok."""
    from unittest.mock import MagicMock

    from aidevswarm.crews.planning.crew import CrewaiPlanningCrew

    log = MagicMock()
    # All-bad: nothing parseable -> empty list, caller turns it into ValueError.
    specs = CrewaiPlanningCrew._parse_specs({"milestones": [{"not": "a milestone"}]}, log)
    assert specs == []
    log.warning.assert_called()  # the skip warning fired

    # Bad JSON string -> handled, returns empty list.
    specs2 = CrewaiPlanningCrew._parse_specs("{not-json", log)
    assert specs2 == []

    # Non-dict input (rare) -> empty.
    specs3 = CrewaiPlanningCrew._parse_specs([1, 2, 3], log)
    assert specs3 == []
