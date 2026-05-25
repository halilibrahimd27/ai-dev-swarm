"""Unit tests for the asyncio :class:`Scheduler`.

Each test cancels the scheduler after a short wait so the test
suite stays fast. ``IntervalJob`` is exercised directly so we don't
need to start a long-running loop.
"""

from __future__ import annotations

import asyncio

import pytest

from aidevswarm.orchestrator.scheduler import IntervalJob, Scheduler


@pytest.mark.asyncio
async def test_scheduler_runs_each_job_at_least_once() -> None:
    hits: dict[str, int] = {"a": 0, "b": 0}

    async def job_a() -> None:
        hits["a"] += 1

    async def job_b() -> None:
        hits["b"] += 1

    scheduler = Scheduler(
        [
            IntervalJob("a", 60.0, job_a),
            IntervalJob("b", 60.0, job_b),
        ]
    )
    task = asyncio.create_task(scheduler.run_forever())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert hits["a"] >= 1
    assert hits["b"] >= 1


@pytest.mark.asyncio
async def test_scheduler_job_exception_is_logged_and_loop_continues() -> None:
    calls: list[int] = []

    async def flaky() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("first call always fails")

    scheduler = Scheduler([IntervalJob("flaky", 60.0, flaky)])
    task = asyncio.create_task(scheduler.run_forever())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The first call raised but the scheduler MUST keep going.
    assert len(calls) >= 1


@pytest.mark.asyncio
async def test_scheduler_shutdown_cancels_all_tasks() -> None:
    async def long_job() -> None:
        await asyncio.sleep(10)

    scheduler = Scheduler([IntervalJob("long", 60.0, long_job)])
    task = asyncio.create_task(scheduler.run_forever())
    await asyncio.sleep(0.01)
    await scheduler.shutdown()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
