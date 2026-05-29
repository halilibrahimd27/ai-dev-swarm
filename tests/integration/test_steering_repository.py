"""Integration test for :class:`PsycopgSteeringRepo` against live Postgres.

Skips when Postgres isn't reachable. Creates a throwaway project row,
exercises the full add → pull cycle, and confirms the atomic delivery
semantics by issuing two concurrent pulls.
"""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

from aidevswarm.steering.repository import PsycopgSteeringRepo

# ``live_pool`` comes from tests/integration/conftest.py (isolated test DB).

pytestmark = pytest.mark.integration


@pytest.fixture
def project_row(live_pool: ConnectionPool) -> Iterator[UUID]:
    """Insert a throwaway project row and clean it up after the test."""
    pid = uuid4()
    spec = {
        "title": "steering-test",
        "summary": "smoke",
        "rationale": "smoke",
        "stack": [],
        "tags": [],
        "score": 80,
    }
    with live_pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO projects (id, name, spec, state)
            VALUES (%s, %s, %s, 'queued')
            """,
            (str(pid), f"steering-test-{pid}", Json(spec)),
        )
    try:
        yield pid
    finally:
        with live_pool.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM projects WHERE id = %s", (str(pid),))


def test_add_then_pull_round_trip(live_pool: ConnectionPool, project_row: UUID) -> None:
    repo = PsycopgSteeringRepo(live_pool)
    repo.add_note(project_row, "be terse")
    repo.add_note(project_row, "prefer Rust where it makes sense")

    bodies = repo.pull_unconsumed(project_row, "Ideator")
    assert bodies == ["be terse", "prefer Rust where it makes sense"]
    # Second pull is empty — atomic deliver-once.
    assert repo.pull_unconsumed(project_row, "Ideator") == []


def test_concurrent_pulls_each_note_delivered_at_most_once(
    live_pool: ConnectionPool, project_row: UUID
) -> None:
    """Two threads pulling for the same project must not both see the same note.

    Verifies the ``FOR UPDATE SKIP LOCKED`` + transaction guarantees:
    the loser sees an empty list rather than a duplicate.
    """
    repo = PsycopgSteeringRepo(live_pool)
    for i in range(5):
        repo.add_note(project_row, f"note {i}")

    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(repo.pull_unconsumed, project_row, "RoleA")
        f2 = executor.submit(repo.pull_unconsumed, project_row, "RoleB")
        a = f1.result(timeout=5)
        b = f2.result(timeout=5)

    seen = sorted(a + b)
    assert seen == sorted([f"note {i}" for i in range(5)])
    # No body appears in both pulls.
    assert set(a).isdisjoint(set(b))


def test_empty_body_rejected(live_pool: ConnectionPool, project_row: UUID) -> None:
    repo = PsycopgSteeringRepo(live_pool)
    with pytest.raises(ValueError):
        repo.add_note(project_row, "   ")
