"""FastAPI + SSE control plane.

Bound exclusively to ``127.0.0.1`` (or ``localhost``); the Settings
layer refuses any other host at startup. Exposes:

  * ``GET /healthz``                 — liveness.
  * ``GET /api/projects``            — list typed Project rows.
  * ``GET /api/projects/{project_id}`` — one project + milestones.
  * ``POST /api/commands``           — accept a typed Command from
    the web UI; route through CommandRouter (the Telegram bot uses
    the same router).
  * ``GET /sse/projects``            — projects-topic stream.
  * ``GET /sse/transcript/{project_id}`` — live inter-agent transcript
    filtered to one project.
  * ``GET /sse/metrics``             — metrics stream.
  * ``GET /``                        — StaticFiles mount serving ``ui/``.

Every SSE message is passed through :class:`SecretRedactor` before
hitting the wire — the test gauntlet asserts a deliberate
``sk-ant-...`` substring is redacted on both SSE and Telegram outbound.

The server is wired into the orchestrator's ``asyncio.gather`` as
another coroutine via :class:`uvicorn.Server`. The legacy
single-loop model is preserved — FastAPI shares the event loop with
the Scheduler and ProjectPool.
"""

from __future__ import annotations

import asyncio
import hmac
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import TypeAdapter, ValidationError
from sse_starlette.sse import EventSourceResponse

from aidevswarm.db.protocols import (
    IdeaEvaluationRepo,
    MilestoneRepo,
    ProjectRepo,
    TokenLogRepo,
)
from aidevswarm.db.transcript import TranscriptRepo
from aidevswarm.logging_config import get_logger
from aidevswarm.observability import DECISION_KIND, EventBridge, SecretRedactor, Topic
from aidevswarm.orchestrator.command_router import CommandResult, CommandRouter
from aidevswarm.schemas import Command, Milestone, Project
from aidevswarm.settings import Settings

_COMMAND_ADAPTER: TypeAdapter[Command] = TypeAdapter(Command)

# Hostnames a browser Origin/Host may legitimately carry for a loopback-only
# server. A cross-site page (evil.com) or a DNS-rebinding name resolving to
# 127.0.0.1 carries a DIFFERENT origin host, so this set defeats both.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]", "0.0.0.0"})  # nosec B104
# State-changing methods that must pass the Origin guard + token check.
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _origin_host(value: str | None) -> str | None:
    """Hostname of an Origin/Referer header value, or None if absent."""
    if not value:
        return None
    return urlsplit(value).hostname


def _bearer_token(value: str | None) -> str | None:
    """The token from an ``Authorization: Bearer <token>`` header."""
    if not value:
        return None
    prefix = "bearer "
    return value[len(prefix) :].strip() if value.lower().startswith(prefix) else None


def build_app(
    *,
    settings: Settings,
    project_repo: ProjectRepo,
    milestone_repo: MilestoneRepo,
    bridge: EventBridge,
    router: CommandRouter,
    redactor: SecretRedactor,
    token_repo: TokenLogRepo | None = None,
    idea_repo: IdeaEvaluationRepo | None = None,
    transcript_repo: TranscriptRepo | None = None,
    ui_dir: Path | None = None,
) -> FastAPI:
    """Wire a FastAPI application with all Phase 5 dependencies.

    The composition root (orchestrator.orchestrator) calls this once
    at startup; integration tests call it with in-memory fakes.
    """
    log = get_logger(__name__)
    app = FastAPI(
        title="ai-dev-swarm control plane",
        version="0.5.0",
        # Keep the loopback host visible in the OpenAPI servers list
        # so the operator can copy it from /docs.
        servers=[{"url": f"http://{settings.api_host}:{settings.api_port}"}],
    )

    api_token = settings.api_token.get_secret_value() if settings.api_token else None
    # Fixed-window rate limiter for mutating requests (in-memory; resets on
    # restart — fine for a single-operator local system). _rl holds the
    # current window's [start_monotonic, count].
    rate_limit = settings.api_rate_limit_per_min
    _rl: list[float] = [0.0, 0.0]

    def _rate_limited() -> bool:
        if rate_limit <= 0:
            return False
        now = monotonic()
        if now - _rl[0] >= 60.0:
            _rl[0], _rl[1] = now, 0.0
        _rl[1] += 1
        return _rl[1] > rate_limit

    # ------------------------------------------------------------------
    # Auth guard — Origin/CSRF + optional bearer token
    # ------------------------------------------------------------------
    # The control plane is loopback-only, but loopback alone does NOT stop a
    # malicious web page (cross-site POST, or DNS-rebinding) from driving the
    # API via the operator's browser. For every state-changing request we:
    #   1. reject a cross-origin request (Origin host not loopback), and
    #   2. require a bearer token when AIDEVSWARM_API_TOKEN is set.
    # GET/SSE stay open: cross-origin reads are already blocked by the
    # same-origin policy (we emit no CORS headers).

    @app.middleware("http")
    async def _auth_guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method in _MUTATING_METHODS:
            origin_host = _origin_host(request.headers.get("origin"))
            if origin_host is not None and origin_host not in _LOOPBACK_HOSTS:
                log.warning("api.cross_origin_refused", origin_host=origin_host)
                return JSONResponse(status_code=403, content={"detail": "cross-origin refused"})
            if _rate_limited():
                log.warning("api.rate_limited", limit_per_min=rate_limit)
                return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})
            if api_token is not None:
                provided = _bearer_token(request.headers.get("authorization"))
                if provided is None or not hmac.compare_digest(provided, api_token):
                    return JSONResponse(
                        status_code=401, content={"detail": "missing or invalid API token"}
                    )
        return await call_next(request)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "phase": "5"}

    # ------------------------------------------------------------------
    # REST: projects + milestones
    # ------------------------------------------------------------------

    @app.get("/api/projects", response_model=list[Project])
    async def list_projects() -> list[Project]:
        # ProjectRepo is sync — bounce through to_thread so the loop
        # stays responsive under DB latency.
        return await asyncio.to_thread(_collect_projects, project_repo)

    @app.get("/api/dashboard")
    async def dashboard() -> dict[str, Any]:
        """Enriched project cards for the Dashboard in one call: each project
        with its milestone progress (done/total) + spend so far, so the cards
        can show a progress bar + cost without N per-project fetches."""
        return await asyncio.to_thread(_collect_dashboard, project_repo, milestone_repo, token_repo)

    @app.get("/api/projects/{project_id}")
    async def get_project(project_id: UUID) -> dict[str, Any]:
        project, milestones = await asyncio.to_thread(
            _fetch_project, project_repo, milestone_repo, project_id
        )
        if project is None:
            raise HTTPException(404, "project not found")
        body: dict[str, Any] = {
            "project": project.model_dump(mode="json"),
            "milestones": [m.model_dump(mode="json") for m in milestones],
            "spend": await asyncio.to_thread(_project_spend, token_repo, project_id, milestones),
        }
        return body

    # ------------------------------------------------------------------
    # REST: spend visibility ("where did my money go today?")
    # ------------------------------------------------------------------

    @app.get("/api/spend")
    async def spend() -> dict[str, Any]:
        if token_repo is None:
            return {
                "daily_tokens": 0,
                "daily_cost_usd": 0.0,
                "all_time_tokens": 0,
                "all_time_cost_usd": 0.0,
                "by_role": [],
                "by_project": [],
                "daily_series": [],
            }
        data = await asyncio.to_thread(_collect_spend, token_repo, project_repo)
        return data

    @app.get("/api/ideas")
    async def ideas() -> list[dict[str, Any]]:
        """Recent Critic evaluations — why each idea was accepted/rejected."""
        if idea_repo is None:
            return []
        rows = await asyncio.to_thread(_collect_ideas, idea_repo)
        return rows

    @app.get("/api/settings")
    async def settings_snapshot() -> list[dict[str, Any]]:
        """Editable operational settings + current values (NO secrets).

        Only the allow-listed keys in db.settings_store.EDITABLE_SETTINGS
        are exposed; API keys, the DB password, hosts and pool sizes are
        never returned here. Write via POST /api/commands intent=update_setting.
        """
        from aidevswarm.db.settings_store import snapshot

        return snapshot(settings)

    @app.get("/api/transcript/{project_id}")
    async def transcript_history(project_id: UUID, limit: int = 400) -> list[dict[str, Any]]:
        """Persisted transcript (most-recent ``limit`` entries, chronological)
        replayed on UI load. Capped so a long project's thousands of entries
        don't freeze the page; the live SSE stream carries new ones. Each
        entry is redacted exactly like the SSE path."""
        if transcript_repo is None:
            return []
        capped = max(1, min(limit, 2000))
        entries = await asyncio.to_thread(
            lambda: transcript_repo.list_for_project(project_id, limit=capped)
        )
        return [json.loads(redactor(e.model_dump_json())) for e in entries]

    @app.get("/api/boardroom/{project_id}")
    async def boardroom(project_id: UUID) -> list[dict[str, Any]]:
        """The boardroom stream: only high-level DECISION entries (PM,
        Architect, Reviewer, Finance, Operator) — the company-meeting view,
        not the raw firehose. Replayed on load like the transcript; the live
        SSE stream carries new decisions, which the UI filters client-side."""
        if transcript_repo is None:
            return []
        entries = await asyncio.to_thread(transcript_repo.list_for_project, project_id)
        return [
            json.loads(redactor(e.model_dump_json())) for e in entries if e.kind == DECISION_KIND
        ]

    # ------------------------------------------------------------------
    # REST: commands (shared with Telegram)
    # ------------------------------------------------------------------

    @app.post("/api/commands", response_model=CommandResult)
    async def post_command(request: Request) -> CommandResult:
        raw = await request.json()
        try:
            command = _COMMAND_ADAPTER.validate_python(raw)
        except ValidationError as exc:
            log.info("api.command_invalid", error=str(exc))
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        # Append-only audit trail: one structured (JSON) log line per
        # operator command — intent + a redacted payload + timestamp.
        # Durable via the container's log stream; queryable with the log tooling.
        log.info(
            "api.command_audit",
            intent=command.intent,
            payload=redactor(json.dumps(raw, default=str))[:500],
        )
        # Dispatch is sync but cheap (one DB write at most).
        return await asyncio.to_thread(router.dispatch, command)

    # ------------------------------------------------------------------
    # SSE topics
    # ------------------------------------------------------------------

    async def _emit(topic: Topic, project_id: UUID | None) -> AsyncIterator[dict[str, str]]:
        """Yield SSE-formatted dicts per TranscriptEntry.

        We deliberately do NOT set a per-kind ``event:`` field — that
        would dispatch each message as a *named* SSE event, which the
        browser's ``EventSource.onmessage`` (the default-"message"
        handler the UI uses) never fires for. The kind travels inside the
        JSON ``data`` payload instead, so a single ``onmessage`` handler
        receives every entry.
        """
        async for entry in bridge.stream(topic, project_id=project_id):
            payload = redactor(entry.model_dump_json())
            yield {"id": str(entry.id), "data": payload}

    @app.get("/sse/projects")
    async def sse_projects() -> EventSourceResponse:
        return EventSourceResponse(_emit("projects", None))

    @app.get("/sse/transcript/{project_id}")
    async def sse_transcript(project_id: UUID) -> EventSourceResponse:
        return EventSourceResponse(_emit("transcript", project_id))

    @app.get("/sse/metrics")
    async def sse_metrics() -> EventSourceResponse:
        return EventSourceResponse(_emit("metrics", None))

    # ------------------------------------------------------------------
    # Error handler — always JSON
    # ------------------------------------------------------------------

    @app.exception_handler(ValidationError)
    async def _validation_handler(_: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": exc.errors()})

    # ------------------------------------------------------------------
    # Static UI (mounted last so /api and /sse take precedence).
    # ------------------------------------------------------------------

    if ui_dir is not None and ui_dir.exists():
        index_path = ui_dir / "index.html"

        async def _serve_index() -> HTMLResponse:
            """Serve index.html, injecting the API token (loopback only).

            The token is placed in a ``<meta name="api-token">`` tag the UI
            reads to authenticate its POSTs. Serving it to the local browser
            is acceptable — the server is loopback-only. When no token is
            configured the page is served unchanged.
            """
            html = await asyncio.to_thread(index_path.read_text, "utf-8")
            if api_token is not None:
                meta = f'<meta name="api-token" content="{api_token}">'
                html = html.replace("</head>", f"  {meta}\n</head>", 1)
            return HTMLResponse(html)

        # Registered before the StaticFiles mount so the injected index wins
        # over the raw file for "/" and "/index.html".
        app.get("/")(_serve_index)
        app.get("/index.html")(_serve_index)
        app.mount("/", StaticFiles(directory=str(ui_dir), html=True), name="ui")

    return app


def _collect_projects(project_repo: ProjectRepo) -> list[Project]:
    """Return all projects via the typed ProjectRepo.list_all() method."""
    return project_repo.list_all()


def _collect_spend(token_repo: TokenLogRepo, project_repo: ProjectRepo) -> dict[str, Any]:
    """Today + all-time spend, per-role and per-project (named)."""
    daily_tokens = token_repo.daily_total_tokens()
    daily_cost = token_repo.daily_cost_usd()
    by_role = token_repo.daily_by_role()
    all_tokens, all_cost = token_repo.all_time_totals()
    by_project = token_repo.by_project()
    names = {p.id: p.name for p in project_repo.list_all()}
    return {
        "daily_tokens": daily_tokens,
        "daily_cost_usd": round(daily_cost, 4),
        "all_time_tokens": all_tokens,
        "all_time_cost_usd": round(all_cost, 4),
        "by_role": [{"role": role, "tokens": t, "cost_usd": round(c, 4)} for role, t, c in by_role],
        "by_project": [
            {
                "project_id": str(pid),
                "name": names.get(pid, str(pid)[:8]),
                "tokens": t,
                "cost_usd": round(c, 4),
            }
            for pid, t, c in by_project
        ],
        # 14-day daily cost series for the dashboard sparkline.
        "daily_series": [{"date": d, "cost": c} for d, c in token_repo.daily_cost_series(14)],
    }


def _collect_ideas(idea_repo: IdeaEvaluationRepo) -> list[dict[str, Any]]:
    return [e.model_dump(mode="json") for e in idea_repo.list_recent(limit=60)]


def _collect_dashboard(
    project_repo: ProjectRepo,
    milestone_repo: MilestoneRepo,
    token_repo: TokenLogRepo | None,
) -> dict[str, Any]:
    """Project cards enriched with milestone progress + cost for the Dashboard."""
    cost_by: dict[UUID, float] = {}
    if token_repo is not None:
        cost_by = {pid: c for pid, _t, c in token_repo.by_project()}
    cards: list[dict[str, Any]] = []
    for p in project_repo.list_all():
        ms = milestone_repo.list_for_project(p.id)
        done = sum(1 for m in ms if m.state.value == "done")
        cards.append(
            {
                "id": str(p.id),
                "name": p.name,
                "state": p.state.value,
                "status_detail": p.status_detail,
                "github_repo": p.github_repo,
                "done": done,
                "total": len(ms),
                "cost": round(cost_by.get(p.id, 0.0), 2),
            }
        )
    return {"projects": cards}


def _project_spend(
    token_repo: TokenLogRepo | None,
    project_id: UUID,
    milestones: list[Milestone],
) -> dict[str, Any]:
    """Cost so far + a naive projection to finish (avg done-milestone cost
    times total). Lets the operator see "spent $X, ~$Y to finish" per project."""
    total = len(milestones)
    done = sum(1 for m in milestones if m.state.value == "done")
    cost_so_far = 0.0
    recent_avg = 0.0
    if token_repo is not None:
        cost_so_far = next(
            (round(c, 2) for pid, _t, c in token_repo.by_project() if pid == project_id), 0.0
        )
        recent_avg = token_repo.recent_milestone_avg_cost(project_id)
    # Project the REMAINING milestones at the recent per-milestone cost (which
    # reflects the current model tier), so the estimate isn't skewed by
    # earlier, pricier milestones. Fall back to the all-history average.
    remaining = max(0, total - done)
    if recent_avg > 0:
        projected: float | None = round(cost_so_far + remaining * recent_avg, 2)
    elif done:
        projected = round(cost_so_far / done * total, 2)
    else:
        projected = None
    return {
        "cost_so_far": cost_so_far,
        "done": done,
        "total": total,
        "projected_total": projected,
    }


def _fetch_project(
    project_repo: ProjectRepo,
    milestone_repo: MilestoneRepo,
    project_id: UUID,
) -> tuple[Project | None, list[Milestone]]:
    project = project_repo.get(project_id)
    if project is None:
        return None, []
    milestones = milestone_repo.list_for_project(project_id)
    return project, milestones


async def run_server(app: FastAPI, host: str, port: int) -> None:
    """Run uvicorn inside the orchestrator's asyncio.gather."""
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_config=None,  # let structlog own the format
        loop="asyncio",
        access_log=False,
    )
    server = uvicorn.Server(config)
    await server.serve()


__all__ = ["build_app", "run_server"]
