"""Orchestrator entry point.

``main()`` builds the production dependency graph (psycopg3 repos, real
crews, RedisKillSwitch, DockerSandbox, TelegramNotifier, GitHubPublisher)
and runs the scheduler forever. Tests don't import this module — they
construct :class:`Tick` directly with in-memory fakes.
"""

from __future__ import annotations

import asyncio

from aidevswarm.crews import CrewaiBuildCrew, CrewaiIdeationCrew, CrewaiPlanningCrew
from aidevswarm.db.repositories import (
    PsycopgMilestoneRepo,
    PsycopgProjectRepo,
    PsycopgTokenLogRepo,
)
from aidevswarm.logging_config import configure_logging, get_logger
from aidevswarm.orchestrator.scheduler import IntervalJob, Scheduler
from aidevswarm.orchestrator.tick import Tick, TickDeps
from aidevswarm.settings import load_settings
from aidevswarm.tools import (
    DefaultTokenBudget,
    DockerSandbox,
    GitHubPublisher,
    PgvectorMemory,
    RedisKillSwitch,
    TelegramNotifier,
    WorkspaceManager,
)


def _build_tick(settings: object) -> Tick:
    """Build the production :class:`Tick` from real adapters."""
    from aidevswarm.settings import Settings

    assert isinstance(settings, Settings)

    project_repo = PsycopgProjectRepo(settings)
    milestone_repo = PsycopgMilestoneRepo(settings)
    token_repo = PsycopgTokenLogRepo(settings)
    # PgvectorMemory and budget guard are constructed but unused by the
    # tick path in Phase 0 — the Ideation crew and CrewAI agent layer
    # call them directly in later phases. Hold references so they cannot
    # be garbage-collected mid-tick if the orchestrator is extended.
    _ = PgvectorMemory(settings)
    _ = DefaultTokenBudget(settings, token_repo)

    deps = TickDeps(
        settings=settings,
        project_repo=project_repo,
        milestone_repo=milestone_repo,
        ideation_crew=CrewaiIdeationCrew(settings),
        planning_crew=CrewaiPlanningCrew(settings),
        build_crew=CrewaiBuildCrew(settings),
        workspace_manager=WorkspaceManager(settings.workspaces_dir),
        sandbox=DockerSandbox(),
        telegram=TelegramNotifier(settings),
        github=GitHubPublisher(settings),
        kill_switch=RedisKillSwitch.from_settings(settings),
    )
    return Tick(deps)


async def _async_main() -> None:
    settings = load_settings()
    configure_logging(json_logs=True)
    log = get_logger(__name__)
    log.info("orchestrator.start", tick_seconds=settings.tick_seconds)

    tick = _build_tick(settings)

    async def project_tick() -> None:
        # The tick itself is synchronous; offload so the scheduler stays
        # responsive even if a tick takes seconds (DB / network).
        await asyncio.to_thread(tick.advance_one_step)

    async def ideation_cron() -> None:
        log.info("ideation_cron.run")
        # Phase 0 leaves the ideation refill to operator-driven enqueues;
        # the cron is wired so Phase 3+ can attach the full ideation flow
        # without changing the scheduler topology.

    scheduler = Scheduler(
        jobs=[
            IntervalJob("project_tick", float(settings.tick_seconds), project_tick),
            IntervalJob("ideation_cron", 60.0 * 60.0 * 24.0, ideation_cron),
        ]
    )
    await scheduler.run_forever()


def main() -> None:
    """CLI entry point (``python -m aidevswarm``)."""
    asyncio.run(_async_main())


__all__ = ["main"]
