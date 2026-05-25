"""Asyncio interval scheduler.

Phase 0 has two periodic jobs:

  * ``project_tick`` runs every ``settings.tick_seconds`` and advances
    the one active project by one step.
  * ``ideation_cron`` runs less frequently (default daily) to refill the
    project queue with new ideas.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from aidevswarm.logging_config import get_logger


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
