"""Orchestrator entry point.

``main()`` builds the production dependency graph (pool-backed psycopg3
repos, real CrewAI crews, RedisKillSwitch, DockerSandbox,
TelegramNotifier, GitHubPublisher) and runs the scheduler forever.
Tests don't import this module — they construct :class:`Tick` directly
with in-memory fakes.
"""

from __future__ import annotations

import asyncio

from aidevswarm.crews import CrewaiBuildCrew, CrewaiIdeationCrew, CrewaiPlanningCrew
from aidevswarm.db.pool import close_pool, open_pool
from aidevswarm.db.repositories import (
    PsycopgMilestoneRepo,
    PsycopgProjectRepo,
    PsycopgTokenLogRepo,
)
from aidevswarm.db.sessions import PsycopgMilestoneSessionRepo
from aidevswarm.logging_config import configure_logging, get_logger
from aidevswarm.observability import bootstrap_phoenix
from aidevswarm.orchestrator.scheduler import IntervalJob, Scheduler
from aidevswarm.orchestrator.tick import Tick, TickDeps
from aidevswarm.settings import Settings, load_settings
from aidevswarm.tools import (
    DefaultTokenBudget,
    DockerSandbox,
    GitHubPublisher,
    PgvectorMemory,
    RedisKillSwitch,
    TelegramNotifier,
    WorkspaceManager,
)
from aidevswarm.tools.mcp_config import load_mcp_servers


def _build_tick(settings: Settings) -> Tick:
    """Build the production :class:`Tick` from real adapters."""
    pool = open_pool(settings)

    project_repo = PsycopgProjectRepo(pool)
    milestone_repo = PsycopgMilestoneRepo(pool)
    token_repo = PsycopgTokenLogRepo(pool)
    session_repo = PsycopgMilestoneSessionRepo(pool)

    # PgvectorMemory and budget guard live alongside the tick but are
    # not yet exercised by the Phase 1 tick path — they are wired into
    # CrewAI agents in Phase 3+. Keep references so they cannot be GC'd
    # if a later phase reads them off the orchestrator.
    _ = PgvectorMemory(pool)
    _ = DefaultTokenBudget(settings, token_repo)

    deps = TickDeps(
        settings=settings,
        project_repo=project_repo,
        milestone_repo=milestone_repo,
        ideation_crew=CrewaiIdeationCrew(settings),
        planning_crew=CrewaiPlanningCrew(settings),
        build_crew=CrewaiBuildCrew(settings, session_repo, mcp_servers=load_mcp_servers()),
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

    # Phoenix MUST be wired before any CrewAI Agent is built so every
    # agent call lands in the trace tree.
    bootstrap_phoenix(settings)

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
    try:
        await scheduler.run_forever()
    finally:
        close_pool()


def main() -> None:
    """CLI entry point (``python -m aidevswarm``)."""
    asyncio.run(_async_main())


__all__ = ["main"]
