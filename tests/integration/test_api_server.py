"""Integration tests for the Phase 5 FastAPI control plane.

Uses ``fastapi.testclient.TestClient`` (which spawns an in-process
uvicorn-less ASGI app) so no real port is opened. Two specific
assertions the phase prompt mandates land here:

  * ``GET /healthz`` returns 200 against the loopback host.
  * A ``POST /api/commands`` with an InjectNote payload writes a
    steering note (via the shared CommandRouter).
  * An SSE topic emits a redacted TranscriptEntry — a deliberate
    ``sk-ant-...`` in the source event must not appear in the SSE
    payload.

The "non-loopback host is refused" check lives in the unit
``tests/unit/test_settings.py::test_api_host_loopback_only`` —
no socket bind required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from aidevswarm.api.server import build_app
from aidevswarm.observability import EventBridge, SecretRedactor, TranscriptEntry
from aidevswarm.orchestrator.command_router import CommandRouter
from aidevswarm.schemas import (
    AcceptanceCriterion,
    MilestoneSpec,
    Project,
    ProjectSpec,
)
from aidevswarm.settings import Settings
from aidevswarm.tools.kill_switch import InMemoryKillSwitch
from tests.fakes import InMemoryMilestoneRepo, InMemoryProjectRepo, InMemoryTokenLogRepo

pytestmark = pytest.mark.integration


@dataclass
class _FakeSteeringRepo:
    notes: list[dict[str, Any]] = field(default_factory=list)
    _counter: int = 0

    def add_note(self, project_id: UUID, body: str, *, author: str = "human") -> int:
        self._counter += 1
        self.notes.append(
            {"id": self._counter, "project_id": project_id, "body": body, "author": author}
        )
        return self._counter

    def pull_unconsumed(self, project_id: UUID, role: str) -> list[str]:
        return []


def _spec() -> ProjectSpec:
    return ProjectSpec(
        title="t", summary="s", rationale="r", stack=["python"], tags=["x"], score=85
    )


def _build(
    *, ui_dir: Path | None = None
) -> tuple[Any, InMemoryProjectRepo, InMemoryMilestoneRepo, _FakeSteeringRepo, EventBridge]:
    settings = Settings(AIDEVSWARM_API_HOST="127.0.0.1", AIDEVSWARM_API_PORT=18080)
    project_repo = InMemoryProjectRepo()
    milestone_repo = InMemoryMilestoneRepo()
    steering = _FakeSteeringRepo()
    kill = InMemoryKillSwitch()
    bridge = EventBridge()
    router = CommandRouter(
        project_repo=project_repo,
        steering_repo=steering,
        kill_switch=kill,
    )
    redactor = SecretRedactor(settings.redact_patterns)
    app = build_app(
        settings=settings,
        project_repo=project_repo,
        milestone_repo=milestone_repo,
        bridge=bridge,
        router=router,
        redactor=redactor,
        ui_dir=ui_dir,
    )
    return app, project_repo, milestone_repo, steering, bridge


def test_healthz_returns_ok() -> None:
    app, *_ = _build()
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_list_projects_returns_existing_rows() -> None:
    app, project_repo, *_ = _build()
    project_repo.create(Project(name="alpha", spec=_spec()))
    project_repo.create(Project(name="bravo", spec=_spec()))
    with TestClient(app) as client:
        response = client.get("/api/projects")
    assert response.status_code == 200
    names = [p["name"] for p in response.json()]
    assert set(names) == {"alpha", "bravo"}


def test_get_one_project_returns_milestones() -> None:
    app, project_repo, milestone_repo, *_ = _build()
    project = project_repo.create(Project(name="alpha", spec=_spec()))
    milestone_repo.create_many(
        project.id,
        [
            MilestoneSpec(
                title=f"m{i}",
                description="d",
                acceptance_criteria=[AcceptanceCriterion(description="ok", verifier="pytest")],
            )
            for i in range(2)
        ],
    )
    with TestClient(app) as client:
        response = client.get(f"/api/projects/{project.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["project"]["name"] == "alpha"
    assert len(body["milestones"]) == 2


def test_get_unknown_project_404s() -> None:
    app, *_ = _build()
    with TestClient(app) as client:
        response = client.get(f"/api/projects/{uuid4()}")
    assert response.status_code == 404


def test_post_inject_note_writes_a_steering_row() -> None:
    app, project_repo, _, steering, _ = _build()
    project = project_repo.create(Project(name="p", spec=_spec()))
    payload = {
        "intent": "inject_note",
        "project_id": str(project.id),
        "body": "focus on tests",
    }
    with TestClient(app) as client:
        response = client.post("/api/commands", json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ok"] is True
    assert len(steering.notes) == 1
    assert steering.notes[0]["body"] == "focus on tests"


def test_post_unknown_intent_is_422() -> None:
    app, *_ = _build()
    with TestClient(app) as client:
        response = client.post(
            "/api/commands", json={"intent": "delete_everything", "scope": "all"}
        )
    assert response.status_code == 422


def test_post_destructive_unconfirmed_returns_requires_confirmation() -> None:
    app, project_repo, *_ = _build()
    project = project_repo.create(Project(name="p", spec=_spec()))
    with TestClient(app) as client:
        response = client.post(
            "/api/commands",
            json={"intent": "abort_project", "project_id": str(project.id)},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["requires_confirmation"] is True


def test_redactor_wrapping_an_event_strips_secrets() -> None:
    """Phase 5 invariant: a sk-ant-... in a payload MUST be redacted.

    Verifies the same code path SSE uses (redactor(entry.model_dump_json()))
    without going through the full ASGI/SSE wire — that path is exercised
    end-to-end in tests/integration/test_redact_wraps_outbound.py.
    """
    settings = Settings()
    redactor = SecretRedactor(settings.redact_patterns)
    leaky = TranscriptEntry(
        topic="transcript",
        project_id=uuid4(),
        kind="llm_chunk",
        text="leak sk-ant-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa here",
    )
    payload = redactor(leaky.model_dump_json())
    assert "sk-ant-aaaaa" not in payload
    assert "[REDACTED:anthropic]" in payload


def test_spend_endpoint_without_repo_returns_zeros() -> None:
    app, *_ = _build()
    with TestClient(app) as client:
        response = client.get("/api/spend")
    assert response.status_code == 200
    body = response.json()
    assert body == {"daily_tokens": 0, "daily_cost_usd": 0.0, "by_role": []}


def test_spend_endpoint_reports_per_role_breakdown() -> None:
    settings = Settings(AIDEVSWARM_API_HOST="127.0.0.1", AIDEVSWARM_API_PORT=18080)
    token_repo = InMemoryTokenLogRepo()
    token_repo.record(
        project_id=None,
        milestone_id=None,
        role="Developer",
        model="anthropic/claude-opus-4-7",
        input_tokens=1000,
        output_tokens=500,
        cost_usd=0.30,
    )
    token_repo.record(
        project_id=None,
        milestone_id=None,
        role="Tester",
        model="anthropic/claude-haiku-4-5",
        input_tokens=2000,
        output_tokens=200,
        cost_usd=0.01,
    )
    app = build_app(
        settings=settings,
        project_repo=InMemoryProjectRepo(),
        milestone_repo=InMemoryMilestoneRepo(),
        bridge=EventBridge(),
        router=CommandRouter(
            project_repo=InMemoryProjectRepo(),
            steering_repo=_FakeSteeringRepo(),
            kill_switch=InMemoryKillSwitch(),
        ),
        redactor=SecretRedactor(settings.redact_patterns),
        token_repo=token_repo,
    )
    with TestClient(app) as client:
        response = client.get("/api/spend")
    assert response.status_code == 200
    body = response.json()
    assert body["daily_tokens"] == 3700
    assert body["daily_cost_usd"] == 0.31
    # Most-expensive role first.
    assert body["by_role"][0]["role"] == "Developer"
    assert body["by_role"][0]["cost_usd"] == 0.30


def test_static_ui_mounted_when_directory_exists(tmp_path: Path) -> None:
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    app, *_ = _build(ui_dir=ui)
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "<h1>hi</h1>" in response.text
