"""Asyncio scheduler — interval jobs + Phase-4 N-project worker pool.

Phase 4 splits the scheduler into two pieces that run side-by-side:

  * ``Scheduler`` — interval jobs (e.g. ``ideation_cron``). Each job
    fires on its own cadence; failures are logged and the loop
    continues.
  * ``ProjectPool`` — ``build_concurrency`` asyncio workers, each one
    draining the queued/non-terminal projects via ``Tick.advance_project``.
    Fair share: the oldest project ``created_at`` wins the next worker.
    A project is claimed for the duration of a single
    ``advance_project`` call; multiple workers never touch the same
    project concurrently.

Both interfaces share ``shutdown()`` so the orchestrator entry point
can cancel them cleanly.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from aidevswarm.db.protocols import ProjectRepo
from aidevswarm.logging_config import get_logger
from aidevswarm.orchestrator.tick import Tick
from aidevswarm.schemas import TERMINAL_PROJECT_STATES, Project, ProjectState

# States the scheduler treats as "advanceable" — these projects need
# work from a worker. Excluded by intent: AWAITING_APPROVAL (needs an
# external trigger) and BLOCKED (needs an operator).
_ADVANCEABLE_STATES: frozenset[ProjectState] = frozenset(
    {
        ProjectState.QUEUED,
        ProjectState.PLANNING,
        ProjectState.BUILDING,
        ProjectState.REPLANNING,
        ProjectState.INTEGRATION,
    }
)


# ----------------------------------------------------------------------
# Interval scheduler (Phase 0/1 shape, unchanged)
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class IntervalJob:
    """A single scheduled callable, run on a fixed interval."""

    name: str
    interval_seconds: float
    func: Callable[[], Awaitable[None]]


class Scheduler:
    """Cooperative asyncio scheduler for a small set of long-lived jobs."""

    def __init__(self, jobs: list[IntervalJob]) -> None:
        self._jobs = jobs
        self._log = get_logger(__name__)
        self._tasks: list[asyncio.Task[None]] = []

    async def run_forever(self) -> None:
        """Spawn one task per job and wait forever (or until cancelled)."""
        self._tasks = [asyncio.create_task(self._run_job(j)) for j in self._jobs]
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            self._log.info("scheduler.cancelled")
            raise

    async def _run_job(self, job: IntervalJob) -> None:
        while True:
            try:
                await job.func()
            except Exception as exc:
                self._log.warning("scheduler.job_error", job=job.name, error=str(exc))
            await asyncio.sleep(job.interval_seconds)

    async def shutdown(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


# ----------------------------------------------------------------------
# Phase-4 project-pool scheduler
# ----------------------------------------------------------------------


class ProjectPool:
    """N concurrent workers draining the queued/non-terminal projects.

    A worker's loop is: pick the oldest advanceable, non-claimed
    project; call ``Tick.advance_project`` (offloaded to a thread —
    the tick itself is synchronous); release the claim. The worker
    polls every ``poll_seconds`` when the queue is empty.
    """

    def __init__(
        self,
        *,
        tick: Tick,
        project_repo: ProjectRepo,
        concurrency: int = 1,
        poll_seconds: float = 1.0,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        self._tick = tick
        self._repo = project_repo
        self._concurrency = concurrency
        self._poll = poll_seconds
        self._log = get_logger(__name__)
        self._claimed: set[UUID] = set()
        self._claim_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task[None]] = []

    async def run_forever(self) -> None:
        self._tasks = [asyncio.create_task(self._worker(i)) for i in range(self._concurrency)]
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            self._log.info("project_pool.cancelled")
            raise

    async def drain_once(self) -> int:
        """One scheduling cycle: every worker tries once. Returns calls made.

        Useful for tests that want deterministic step counts.
        """
        results = await asyncio.gather(*(self._step(i) for i in range(self._concurrency)))
        return sum(1 for r in results if r is True)

    async def shutdown(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _worker(self, worker_id: int) -> None:
        while True:
            advanced = await self._step(worker_id)
            if not advanced:
                await asyncio.sleep(self._poll)

    async def _step(self, worker_id: int) -> bool:
        """Try to advance one project; True if we did, False if idle."""
        project = await self._claim_next()
        if project is None:
            return False
        try:
            self._log.debug(
                "project_pool.advance",
                worker=worker_id,
                project=project.name,
                state=project.state.value,
            )
            try:
                updated = await asyncio.to_thread(self._tick.advance_project, project)
            except Exception as exc:
                # One crashing crew (parser, LLM, anything) MUST NOT take
                # down the whole orchestrator — that's the crash-loop the
                # operator hit on day 1. Park the project as BLOCKED so
                # the pool stops picking it up, and the operator can
                # rescope or abort via the web panel.
                self._log.warning(
                    "project_pool.advance_failed",
                    worker=worker_id,
                    project=project.name,
                    state=project.state.value,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                try:
                    self._repo.update_state(project.id, ProjectState.BLOCKED)
                except Exception as inner:
                    # Even the safety-net move can race; log + continue.
                    self._log.error(
                        "project_pool.block_failed",
                        project=project.name,
                        error=str(inner),
                    )
                return True
            if updated is not None and updated.state in TERMINAL_PROJECT_STATES:
                self._log.info(
                    "project_pool.terminal",
                    project=project.name,
                    state=updated.state.value,
                )
        finally:
            async with self._claim_lock:
                self._claimed.discard(project.id)
        return True

    async def _claim_next(self) -> Project | None:
        """Atomically pick the oldest advanceable project not yet claimed."""
        async with self._claim_lock:
            candidates = self._gather_candidates()
            for project in candidates:
                if project.id not in self._claimed:
                    self._claimed.add(project.id)
                    return project
        return None

    def _gather_candidates(self) -> list[Project]:
        """Sort advanceable projects by ``created_at`` ascending (fair-share)."""
        bag: list[Project] = []
        for state in _ADVANCEABLE_STATES:
            bag.extend(self._repo.list_by_state(state))
        bag.sort(key=lambda p: p.created_at)
        return bag
