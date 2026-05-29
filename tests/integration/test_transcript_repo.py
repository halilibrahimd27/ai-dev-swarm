"""Integration test for :class:`PsycopgTranscriptRepo` against live Postgres.

``live_pool`` (tests/integration/conftest.py) points at the isolated
test DB. Verifies durable append + chronological replay + per-project
isolation — the persistence that lets the UI survive a refresh.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from psycopg_pool import ConnectionPool

from aidevswarm.db.repositories import PsycopgProjectRepo
from aidevswarm.db.transcript import PsycopgTranscriptRepo
from aidevswarm.observability import TranscriptEntry
from aidevswarm.schemas import Project, ProjectSpec

pytestmark = pytest.mark.integration


def _spec() -> ProjectSpec:
    return ProjectSpec(title="t", summary="s", rationale="r", stack=["python"], tags=["x"], score=85)


@pytest.fixture
def project_id(live_pool: ConnectionPool) -> Iterator[object]:
    repo = PsycopgProjectRepo(live_pool)
    p = repo.create(Project(name=f"transcript-test-{uuid4()}", spec=_spec()))
    yield p.id
    with live_pool.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM projects WHERE id = %s", (str(p.id),))


def _entry(pid: object, kind: str, text: str) -> TranscriptEntry:
    return TranscriptEntry(topic="transcript", project_id=pid, role="Developer", kind=kind, text=text)  # type: ignore[arg-type]


def test_append_then_list_in_chronological_order(
    live_pool: ConnectionPool, project_id: object
) -> None:
    repo = PsycopgTranscriptRepo(live_pool)
    repo.append(_entry(project_id, "assistant", "first"))
    repo.append(_entry(project_id, "tool_use", "Edit"))
    repo.append(_entry(project_id, "assistant", "third"))

    rows = repo.list_for_project(project_id)  # type: ignore[arg-type]
    assert [r.text for r in rows] == ["first", "Edit", "third"]
    assert rows[1].kind == "tool_use"
    assert all(r.topic == "transcript" for r in rows)


def test_extra_jsonb_round_trips(live_pool: ConnectionPool, project_id: object) -> None:
    repo = PsycopgTranscriptRepo(live_pool)
    entry = TranscriptEntry(
        topic="transcript",
        project_id=project_id,  # type: ignore[arg-type]
        role="Developer",
        kind="tool_use",
        text="Bash",
        extra={"args": "pytest -q", "is_error": False},
    )
    repo.append(entry)
    [row] = repo.list_for_project(project_id)  # type: ignore[arg-type]
    assert row.extra["args"] == "pytest -q"
    assert row.extra["is_error"] is False


def test_list_is_isolated_per_project(live_pool: ConnectionPool, project_id: object) -> None:
    repo = PsycopgTranscriptRepo(live_pool)
    other = PsycopgProjectRepo(live_pool).create(
        Project(name=f"transcript-other-{uuid4()}", spec=_spec())
    )
    try:
        repo.append(_entry(project_id, "assistant", "mine"))
        repo.append(_entry(other.id, "assistant", "theirs"))
        mine = repo.list_for_project(project_id)  # type: ignore[arg-type]
        assert [r.text for r in mine] == ["mine"]
    finally:
        with live_pool.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM projects WHERE id = %s", (str(other.id),))


def test_limit_returns_most_recent_in_order(
    live_pool: ConnectionPool, project_id: object
) -> None:
    repo = PsycopgTranscriptRepo(live_pool)
    for i in range(5):
        repo.append(_entry(project_id, "assistant", f"m{i}"))
    rows = repo.list_for_project(project_id, limit=2)  # type: ignore[arg-type]
    # The two most-recent, still oldest-first.
    assert [r.text for r in rows] == ["m3", "m4"]
