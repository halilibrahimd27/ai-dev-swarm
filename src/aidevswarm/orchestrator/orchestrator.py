"""Orchestrator entry point.

``main()`` builds the production dependency graph (pool-backed psycopg3
repos, real CrewAI crews, RedisKillSwitch, DockerSandbox,
TelegramNotifier, GitHubPublisher) and runs the scheduler forever.
Tests don't import this module — they construct :class:`Tick` directly
with in-memory fakes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aidevswarm.api import build_app, run_server
from aidevswarm.crews import CrewaiBuildCrew, CrewaiIdeationCrew, CrewaiPlanningCrew
from aidevswarm.crews.ideation.novelty import NoveltyChecker
from aidevswarm.crews.replanning import CrewaiReplanningCrew
from aidevswarm.db.pool import close_pool, open_pool
from aidevswarm.db.protocols import IdeaEvaluationRepo
from aidevswarm.db.repositories import (
    PsycopgIdeaEvaluationRepo,
    PsycopgMilestoneRepo,
    PsycopgProjectRepo,
    PsycopgTokenLogRepo,
)
from aidevswarm.db.sessions import PsycopgMilestoneSessionRepo
from aidevswarm.logging_config import configure_logging, get_logger
from aidevswarm.observability import (
    EventBridge,
    SecretRedactor,
    TranscriptEntry,
    bootstrap_phoenix,
)
from aidevswarm.orchestrator.auto_split import AutoSplitPredictor
from aidevswarm.orchestrator.command_router import CommandRouter
from aidevswarm.orchestrator.scheduler import IntervalJob, ProjectPool, Scheduler
from aidevswarm.orchestrator.tick import Tick, TickDeps
from aidevswarm.schemas import Project, ProjectState
from aidevswarm.settings import Settings, load_settings
from aidevswarm.steering import PsycopgSteeringRepo
from aidevswarm.telegram import HaikuIntentParser, TelegramBot
from aidevswarm.tools import (
    DefaultTokenBudget,
    DockerSandbox,
    GitHubPublisher,
    PgvectorMemory,
    RedisKillSwitch,
    SpendRecorder,
    TelegramNotifier,
    WorkspaceManager,
)
from aidevswarm.tools.mcp_config import load_mcp_servers

# The UI directory ships at the repo root; in the docker image it lands
# at /workspace/ui via the Dockerfile.
_UI_DIR = Path(__file__).resolve().parents[3] / "ui"


def _build_tick(
    settings: Settings,
    *,
    transition_sink: Callable[[Project], None] | None = None,
) -> Tick:
    """Build the production :class:`Tick` from real adapters."""
    pool = open_pool(settings)

    project_repo = PsycopgProjectRepo(pool)
    milestone_repo = PsycopgMilestoneRepo(pool)
    token_repo = PsycopgTokenLogRepo(pool)
    session_repo = PsycopgMilestoneSessionRepo(pool)

    # Spend ledger + budget guard. The recorder writes one token_log
    # row per LLM call (visibility); the guard reads those rows back to
    # pace the day and trip a per-milestone circuit breaker.
    _ = PgvectorMemory(pool)
    recorder = SpendRecorder(token_repo)
    token_budget = DefaultTokenBudget(settings, token_repo)

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
            recorder=recorder,
        ),
        planning_crew=CrewaiPlanningCrew(settings, recorder=recorder),
        build_crew=CrewaiBuildCrew(
            settings, session_repo, mcp_servers=load_mcp_servers(), recorder=recorder
        ),
        replanning_crew=CrewaiReplanningCrew(settings, recorder=recorder),
        auto_split=AutoSplitPredictor(settings, session_repo),
        workspace_manager=WorkspaceManager(settings.workspaces_dir),
        sandbox=DockerSandbox(),
        telegram=TelegramNotifier(settings),
        github=GitHubPublisher(settings),
        kill_switch=RedisKillSwitch.from_settings(settings),
        token_budget=token_budget,
        transition_sink=transition_sink,
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

    # Phase 5 control plane wiring — FastAPI + SSE + Telegram all share
    # one EventBridge + one CommandRouter + one SecretRedactor. Build the
    # bridge BEFORE the tick so each project state transition publishes a
    # `projects` SSE event (the web UI updates live, no polling needed).
    bridge = EventBridge()
    bridge.attach(asyncio.get_running_loop())
    bridge.install_crewai_handlers()

    def _publish_transition(project: Project) -> None:
        bridge.publish(
            TranscriptEntry(
                topic="projects",
                project_id=project.id,
                kind="state",
                text=f"{project.name} → {project.state.value}",
                extra={"name": project.name, "state": project.state.value},
            )
        )

    tick = _build_tick(settings, transition_sink=_publish_transition)
    project_repo = tick._d.project_repo
    milestone_repo = tick._d.milestone_repo
    pool_obj = open_pool(settings)  # already opened in _build_tick; reuse
    steering_repo = PsycopgSteeringRepo(pool_obj)
    idea_repo = PsycopgIdeaEvaluationRepo(pool_obj)

    redactor = SecretRedactor(settings.redact_patterns)
    loop = asyncio.get_running_loop()
    router = CommandRouter(
        project_repo=project_repo,
        steering_repo=steering_repo,
        kill_switch=tick._d.kill_switch,
        ideate_runner=lambda: (loop.create_task(_run_ideation_once(tick, idea_repo, log)), None)[1],
    )
    api_app = build_app(
        settings=settings,
        project_repo=project_repo,
        milestone_repo=milestone_repo,
        bridge=bridge,
        router=router,
        redactor=redactor,
        token_repo=PsycopgTokenLogRepo(pool_obj),
        idea_repo=idea_repo,
        ui_dir=_UI_DIR if _UI_DIR.is_dir() else None,
    )

    async def ideation_cron() -> None:
        # Only ideate when the swarm is IDLE — no active project and an
        # empty queue. This keeps the 24h cron from stockpiling ideas (and
        # burning tokens) when there's already work in flight. ARCHITECTURE
        # wants a few ideas a week, one project building at a time.
        active = await asyncio.to_thread(project_repo.get_active)
        queued = await asyncio.to_thread(project_repo.list_by_state, ProjectState.QUEUED)
        if active is not None or queued:
            log.info(
                "ideation_cron.skip_has_work",
                active=active.name if active else None,
                queued=len(queued),
            )
            return
        log.info("ideation_cron.run")
        await _run_ideation_once(tick, idea_repo, log)

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

    # Scheduler + ProjectPool + FastAPI (+ optionally the Telegram bot)
    # all share this loop. gather() propagates the first failure.
    coros: list[Any] = [
        scheduler.run_forever(),
        project_pool.run_forever(),
        run_server(api_app, settings.api_host, settings.api_port),
    ]
    if settings.telegram_bot_token.get_secret_value() and settings.telegram_allowed_user_ids:
        bot = TelegramBot(
            settings=settings,
            router=router,
            parser=HaikuIntentParser(settings),
            redactor=redactor,
        )
        coros.append(bot.run_polling())
        log.info("telegram.bot_enabled", allowed_users=len(settings.telegram_allowed_user_ids))
    else:
        log.info(
            "telegram.bot_disabled",
            reason="set TELEGRAM_BOT_TOKEN + AIDEVSWARM_TELEGRAM_ALLOWED_USER_IDS to enable",
        )

    try:
        await asyncio.gather(*coros)
    finally:
        close_pool()


async def _run_ideation_once(
    tick: Tick,
    idea_repo: IdeaEvaluationRepo,
    log: Any,
) -> None:
    """Ideate (up to ``ideation_max_rounds``) until an idea clears the gate.

    Each round's scored ideas are persisted as :class:`IdeaEvaluation`
    rows (so the UI can show *why* each was accepted/rejected). An idea
    must score >= ``ideation_min_score`` AND be novel to become a
    project; the first round that yields a winner queues it and stops.
    LLM work runs on a worker thread so the event loop stays responsive.
    """
    settings = tick._d.settings
    for round_num in range(1, settings.ideation_max_rounds + 1):
        log.info("ideation.round.start", round=round_num)
        try:
            scored = await asyncio.to_thread(tick._d.ideation_crew.run)
        except Exception as exc:
            log.warning("ideation.round.failed", round=round_num, error=str(exc))
            continue
        if not scored:
            log.info("ideation.round.empty", round=round_num)
            continue

        passing = [
            s
            for s in scored
            if s.total >= settings.ideation_min_score and s.rejected_reason is None
        ]
        best = max(passing, key=lambda s: s.total) if passing else None

        if best is not None:
            project = _project_from_idea(best)
            await asyncio.to_thread(tick._d.project_repo.create, project)
            await asyncio.to_thread(
                _persist_evaluations, idea_repo, scored, round_num, best, project.id
            )
            log.info(
                "ideation.queued",
                project=project.name,
                score=int(best.total),
                round=round_num,
            )
            return

        await asyncio.to_thread(_persist_evaluations, idea_repo, scored, round_num, None, None)
        log.info("ideation.round.no_pass", round=round_num, count=len(scored))

    log.info("ideation.exhausted", rounds=settings.ideation_max_rounds)


def _persist_evaluations(
    idea_repo: IdeaEvaluationRepo,
    scored: list[Any],
    round_num: int,
    accepted: Any | None,
    project_id: Any | None,
) -> None:
    """Record every scored idea this round with its accept/reject verdict."""
    import contextlib

    from aidevswarm.schemas import IdeaEvaluation

    for s in scored:
        is_accepted = accepted is not None and s is accepted
        # Recording is best-effort; never break ideation over a log write.
        with contextlib.suppress(Exception):
            idea_repo.record(
                IdeaEvaluation.from_scored(
                    s,
                    round=round_num,
                    accepted=is_accepted,
                    project_id=project_id if is_accepted else None,
                )
            )


def _project_from_idea(best: Any) -> Project:
    from aidevswarm.schemas import ProjectSpec

    return Project(
        name=_idea_slug(best.idea.title),
        spec=ProjectSpec(
            title=best.idea.title,
            summary=best.idea.summary,
            rationale=best.idea.rationale,
            stack=list(best.idea.stack),
            tags=list(best.idea.tags),
            score=int(best.total),
        ),
    )


def _idea_slug(title: str) -> str:
    """URL-safe, repo-friendly project name from an idea title."""
    cleaned = "".join(c.lower() if c.isalnum() else "-" for c in title)
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned[:48] or "idea"


def main() -> None:
    """CLI entry point (``python -m aidevswarm``)."""
    asyncio.run(_async_main())


__all__ = ["main"]
