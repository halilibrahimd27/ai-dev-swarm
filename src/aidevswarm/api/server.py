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
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import TypeAdapter, ValidationError
from sse_starlette.sse import EventSourceResponse

from aidevswarm.db.protocols import MilestoneRepo, ProjectRepo
from aidevswarm.logging_config import get_logger
from aidevswarm.observability import EventBridge, SecretRedactor, Topic
from aidevswarm.orchestrator.command_router import CommandResult, CommandRouter
from aidevswarm.schemas import Command, Milestone, Project
from aidevswarm.settings import Settings

_COMMAND_ADAPTER: TypeAdapter[Command] = TypeAdapter(Command)


def build_app(
    *,
    settings: Settings,
    project_repo: ProjectRepo,
    milestone_repo: MilestoneRepo,
    bridge: EventBridge,
    router: CommandRouter,
    redactor: SecretRedactor,
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
        }
        return body

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
        # Dispatch is sync but cheap (one DB write at most).
        return await asyncio.to_thread(router.dispatch, command)

    # ------------------------------------------------------------------
    # SSE topics
    # ------------------------------------------------------------------

    async def _emit(topic: Topic, project_id: UUID | None) -> AsyncIterator[dict[str, str]]:
        """Yield SSE-formatted dicts (id, event, data) per TranscriptEntry."""
        async for entry in bridge.stream(topic, project_id=project_id):
            payload = redactor(entry.model_dump_json())
            yield {"id": str(entry.id), "event": entry.kind, "data": payload}

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
        app.mount("/", StaticFiles(directory=str(ui_dir), html=True), name="ui")

    return app


def _collect_projects(project_repo: ProjectRepo) -> list[Project]:
    """Return all projects via the typed ProjectRepo.list_all() method."""
    return project_repo.list_all()


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
