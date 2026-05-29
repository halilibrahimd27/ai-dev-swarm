"""Transient-error handling in the ProjectPool.

An LLM transport / availability failure (rate limit, the Claude Agent SDK
subprocess exiting non-zero, a dropped connection) is NOT a milestone
failure — the code may be fine, the API was just unreachable. The pool
pauses + backs off on these instead of hard-blocking, and only escalates
to BLOCKED after a persistent streak.
"""

from __future__ import annotations

from typing import Any

import pytest

from aidevswarm.orchestrator.scheduler import _MAX_TRANSIENT_FAILS, ProjectPool
from aidevswarm.schemas import Project, ProjectSpec, ProjectState
from tests.fakes import InMemoryProjectRepo


class ProcessError(Exception):
    """Mirrors claude_agent_sdk.ProcessError by NAME (what the pool keys on)."""


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _TransientTick:
    """Raises a transient error until ``stop_failing`` flips."""

    def __init__(self, repo: InMemoryProjectRepo) -> None:
        self._d = type("Deps", (), {"project_repo": repo})()
        self.stop_failing = False
        self.calls = 0

    def advance_project(self, project: Any) -> Any:
        self.calls += 1
        if self.stop_failing:
            return project  # a clean advance
        raise ProcessError("Command failed with exit code 1")


def _project(name: str = "victim") -> Project:
    return Project(
        name=name,
        state=ProjectState.PLANNING,
        spec=ProjectSpec(
            title=name, summary="s", rationale="r", stack=["python"], tags=["x"], score=85
        ),
    )


@pytest.mark.asyncio
async def test_transient_error_backs_off_not_blocks() -> None:
    repo = InMemoryProjectRepo()
    project = repo.create(_project())
    clock = _Clock()
    pool = ProjectPool(
        tick=_TransientTick(repo),  # type: ignore[arg-type]
        project_repo=repo,
        poll_seconds=0.001,
        clock=clock,
    )
    await pool.drain_once()
    # NOT blocked — still advanceable, just cooling down.
    assert repo.get(project.id).state is ProjectState.PLANNING
    # On cooldown now → the next drain skips it (idle).
    assert await pool.drain_once() == 0
    # Once the cooldown elapses it is retried.
    clock.now += 10_000
    assert await pool.drain_once() >= 1


@pytest.mark.asyncio
async def test_persistent_transient_streak_escalates_to_blocked() -> None:
    repo = InMemoryProjectRepo()
    project = repo.create(_project())
    clock = _Clock()
    pool = ProjectPool(
        tick=_TransientTick(repo),  # type: ignore[arg-type]
        project_repo=repo,
        poll_seconds=0.001,
        clock=clock,
    )
    for _ in range(_MAX_TRANSIENT_FAILS):
        await pool.drain_once()
        clock.now += 10_000  # skip past each cooldown so the next drain runs
    assert repo.get(project.id).state is ProjectState.BLOCKED
    assert "transient" in (repo.get(project.id).status_detail or "")


@pytest.mark.asyncio
async def test_clean_tick_resets_the_backoff_streak() -> None:
    repo = InMemoryProjectRepo()
    project = repo.create(_project())
    clock = _Clock()
    tick = _TransientTick(repo)
    pool = ProjectPool(
        tick=tick,  # type: ignore[arg-type]
        project_repo=repo,
        poll_seconds=0.001,
        clock=clock,
    )
    await pool.drain_once()  # one transient failure recorded
    tick.stop_failing = True
    clock.now += 10_000
    await pool.drain_once()  # clean advance → streak cleared
    assert project.id not in pool._transient_fails
    assert project.id not in pool._cooldown_until


@pytest.mark.asyncio
async def test_non_transient_error_still_blocks_immediately() -> None:
    """A genuine crew bug (not transport) blocks on the first failure."""

    class _BugTick:
        def __init__(self, repo: InMemoryProjectRepo) -> None:
            self._d = type("Deps", (), {"project_repo": repo})()

        def advance_project(self, project: Any) -> Any:
            raise ValueError("bad milestone JSON")  # not transient

    repo = InMemoryProjectRepo()
    project = repo.create(_project())
    pool = ProjectPool(
        tick=_BugTick(repo),  # type: ignore[arg-type]
        project_repo=repo,
        poll_seconds=0.001,
    )
    await pool.drain_once()
    assert repo.get(project.id).state is ProjectState.BLOCKED
