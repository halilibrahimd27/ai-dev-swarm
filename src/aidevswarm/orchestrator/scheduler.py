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
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from aidevswarm.db.protocols import ProjectRepo
from aidevswarm.logging_config import get_logger
from aidevswarm.orchestrator.tick import Tick
from aidevswarm.schemas import TERMINAL_PROJECT_STATES, Project, ProjectState

# A crash whose type/message looks like an LLM transport / availability
# problem (rate limit, overload, the Claude Agent SDK subprocess exiting
# non-zero, a dropped connection) is NOT a milestone-quality failure — the
# code may be perfectly fine, the API was just unreachable. We pause +
# back off on these instead of hard-blocking; only a PERSISTENT streak
# (see ``_MAX_TRANSIENT_FAILS``) escalates to BLOCKED.
_TRANSIENT_ERROR_TYPES: frozenset[str] = frozenset(
    {
        "ProcessError",  # claude_agent_sdk: the `claude` CLI exited non-zero
        "CLIConnectionError",  # claude_agent_sdk: lost the CLI connection
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
        "APIStatusError",
        "OverloadedError",
    }
)
_TRANSIENT_MSG_HINTS: tuple[str, ...] = (
    "rate limit",
    "rate_limit",
    "429",
    "529",
    "overloaded",
    "timed out",
    "timeout",
    "temporarily unavailable",
    "connection error",
    "connection reset",
)
# Backoff schedule for transient errors: 30s, 60s, 120s, … capped at 15min.
_BACKOFF_BASE_SECONDS = 30.0
_BACKOFF_CAP_SECONDS = 900.0
# After this many CONSECUTIVE transient failures on one project, give up
# the optimistic retry and block it for a human — a streak this long is no
# longer "transient" (broken env, exhausted daily quota, etc.).
_MAX_TRANSIENT_FAILS = 5


def _is_transient_error(exc: Exception) -> bool:
    """True when ``exc`` looks like an LLM transport/availability blip."""
    if type(exc).__name__ in _TRANSIENT_ERROR_TYPES:
        return True
    msg = str(exc).lower()
    return any(hint in msg for hint in _TRANSIENT_MSG_HINTS)


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
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        self._tick = tick
        self._repo = project_repo
        self._concurrency = concurrency
        self._poll = poll_seconds
        self._clock = clock
        self._log = get_logger(__name__)
        self._claimed: set[UUID] = set()
        self._claim_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task[None]] = []
        # Transient-error backoff (in-memory; resets on restart, which is
        # fine — a restart is itself a fresh attempt). Maps project id ->
        # earliest monotonic time it may be claimed again, and -> the
        # consecutive transient-failure count.
        self._cooldown_until: dict[UUID, float] = {}
        self._transient_fails: dict[UUID, int] = {}

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
        advanced = False
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
                # operator hit on day 1.
                self._log.warning(
                    "project_pool.advance_failed",
                    worker=worker_id,
                    project=project.name,
                    state=project.state.value,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                self._handle_crash(project, exc)
                return True
            # A clean tick (advanced or a deliberate idle) means no transient
            # failure — reset this project's backoff streak.
            self._clear_transient(project.id)
            # `updated is None` means the tick deliberately did NOT advance
            # (kill switch, awaiting approval, or a daily-budget pause).
            # Report idle so the worker backs off `poll_seconds` instead of
            # busy-spinning on the same project.
            advanced = updated is not None
            if updated is not None and updated.state in TERMINAL_PROJECT_STATES:
                self._log.info(
                    "project_pool.terminal",
                    project=project.name,
                    state=updated.state.value,
                )
        finally:
            async with self._claim_lock:
                self._claimed.discard(project.id)
        return advanced

    def _handle_crash(self, project: Project, exc: Exception) -> None:
        """Decide whether a crashed tick is a transient blip or a real block.

        Transient (rate limit / SDK transport / API unavailable): leave the
        project advanceable but put it on an exponential cooldown so the
        pool stops hammering an API that's saying no. Only a persistent
        streak escalates to BLOCKED. Anything else blocks immediately.
        """
        if _is_transient_error(exc) and self._transient_fails.get(project.id, 0) + 1 < (
            _MAX_TRANSIENT_FAILS
        ):
            n = self._transient_fails.get(project.id, 0) + 1
            self._transient_fails[project.id] = n
            backoff = min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * (2 ** (n - 1)))
            self._cooldown_until[project.id] = self._clock() + backoff
            self._log.warning(
                "project_pool.transient_backoff",
                project=project.name,
                error_type=type(exc).__name__,
                attempt=n,
                backoff_seconds=int(backoff),
            )
            # Leave the project state untouched (still advanceable); just
            # note the wait so the operator isn't confused by the stall.
            self._safe_status(
                project.id,
                f"paused {int(backoff)}s: {type(exc).__name__} (likely API rate "
                f"limit / transport) — retry {n}/{_MAX_TRANSIENT_FAILS}",
            )
            return
        # Non-transient, or the transient streak is exhausted → block for a
        # human. The pool stops picking it up; resume clears the backoff.
        self._clear_transient(project.id)
        reason = (
            f"repeated transient failures ({_MAX_TRANSIENT_FAILS}x), last "
            if _is_transient_error(exc)
            else f"crashed in {project.state.value}: "
        )
        try:
            self._repo.update_state(project.id, ProjectState.BLOCKED)
            self._safe_status(project.id, f"{reason}{type(exc).__name__}: {str(exc)[:280]}")
        except Exception as inner:  # even the safety-net move can race
            self._log.error("project_pool.block_failed", project=project.name, error=str(inner))

    def _safe_status(self, project_id: UUID, detail: str) -> None:
        try:
            self._repo.set_status_detail(project_id, detail)
        except Exception as inner:  # pragma: no cover — defensive
            self._log.error("project_pool.status_failed", error=str(inner))

    def _clear_transient(self, project_id: UUID) -> None:
        self._cooldown_until.pop(project_id, None)
        self._transient_fails.pop(project_id, None)

    async def _claim_next(self) -> Project | None:
        """Atomically pick the oldest advanceable project not yet claimed.

        Projects on a transient-error cooldown are skipped until their
        cooldown elapses.
        """
        now = self._clock()
        async with self._claim_lock:
            candidates = self._gather_candidates()
            for project in candidates:
                if project.id in self._claimed:
                    continue
                if self._cooldown_until.get(project.id, 0.0) > now:
                    continue  # still backing off after a transient failure
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
