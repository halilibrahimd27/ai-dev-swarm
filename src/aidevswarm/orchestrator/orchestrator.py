"""Orchestrator entry point.

``main()`` builds the production dependency graph (pool-backed psycopg3
repos, real CrewAI crews, RedisKillSwitch, DockerSandbox,
TelegramNotifier, GitHubPublisher) and runs the scheduler forever.
Tests don't import this module — they construct :class:`Tick` directly
with in-memory fakes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from aidevswarm.api import build_app, run_server
from aidevswarm.crews import CrewaiBuildCrew, CrewaiIdeationCrew, CrewaiPlanningCrew
from aidevswarm.crews.ideation.novelty import NoveltyChecker
from aidevswarm.crews.replanning import CrewaiReplanningCrew
from aidevswarm.db.pool import close_pool, open_pool
from aidevswarm.db.repositories import (
    PsycopgMilestoneRepo,
    PsycopgProjectRepo,
    PsycopgTokenLogRepo,
)
from aidevswarm.db.sessions import PsycopgMilestoneSessionRepo
from aidevswarm.logging_config import configure_logging, get_logger
from aidevswarm.observability import EventBridge, SecretRedactor, bootstrap_phoenix
from aidevswarm.orchestrator.auto_split import AutoSplitPredictor
from aidevswarm.orchestrator.command_router import CommandRouter
from aidevswarm.orchestrator.scheduler import IntervalJob, ProjectPool, Scheduler
from aidevswarm.orchestrator.tick import Tick, TickDeps
from aidevswarm.settings import Settings, load_settings
from aidevswarm.steering import PsycopgSteeringRepo
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

# The UI directory ships at the repo root; in the docker image it lands
# at /workspace/ui via the Dockerfile.
_UI_DIR = Path(__file__).resolve().parents[3] / "ui"


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
        session_repo=session_repo,
        ideation_crew=CrewaiIdeationCrew(
            settings,
            novelty_checker=NoveltyChecker(
                github_token=settings.github_token.get_secret_value() or None
            ),
        ),
        planning_crew=CrewaiPlanningCrew(settings),
        build_crew=CrewaiBuildCrew(settings, session_repo, mcp_servers=load_mcp_servers()),
        replanning_crew=CrewaiReplanningCrew(settings),
        auto_split=AutoSplitPredictor(settings, session_repo),
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
    project_repo = tick._d.project_repo
    milestone_repo = tick._d.milestone_repo
    pool_obj = open_pool(settings)  # already opened in _build_tick; reuse
    steering_repo = PsycopgSteeringRepo(pool_obj)

    # Phase 5 control plane wiring — FastAPI + SSE + Telegram all share
    # one EventBridge + one CommandRouter + one SecretRedactor.
    bridge = EventBridge()
    bridge.attach(asyncio.get_running_loop())
    bridge.install_crewai_handlers()
    redactor = SecretRedactor(settings.redact_patterns)
    router = CommandRouter(
        project_repo=project_repo,
        steering_repo=steering_repo,
        kill_switch=tick._d.kill_switch,
    )
    api_app = build_app(
        settings=settings,
        project_repo=project_repo,
        milestone_repo=milestone_repo,
        bridge=bridge,
        router=router,
        redactor=redactor,
        ui_dir=_UI_DIR if _UI_DIR.is_dir() else None,
    )

    async def ideation_cron() -> None:
        log.info("ideation_cron.run")
        # Phase 0 leaves the ideation refill to operator-driven enqueues;
        # Phase 3+ may attach the full ideation flow without changing the
        # scheduler topology.

    scheduler = Scheduler(
        jobs=[
            IntervalJob("ideation_cron", 60.0 * 60.0 * 24.0, ideation_cron),
        ]
    )
    project_pool = ProjectPool(
        tick=tick,
        project_repo=project_repo,
        concurrency=settings.build_concurrency,
        poll_seconds=float(settings.tick_seconds),
    )
    try:
        # Scheduler + ProjectPool + FastAPI all live on the same loop.
        # gather() propagates the first failure to the operator.
        await asyncio.gather(
            scheduler.run_forever(),
            project_pool.run_forever(),
            run_server(api_app, settings.api_host, settings.api_port),
        )
    finally:
        close_pool()


def main() -> None:
    """CLI entry point (``python -m aidevswarm``)."""
    asyncio.run(_async_main())


__all__ = ["main"]
