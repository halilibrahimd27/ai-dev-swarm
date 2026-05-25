"""Integration test for :class:`PsycopgMilestoneSessionRepo`.

Skips when Postgres isn't reachable.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

from aidevswarm.db.pool import close_pool, open_pool
from aidevswarm.db.sessions import PsycopgMilestoneSessionRepo
from aidevswarm.settings import Settings

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def live_pool() -> Iterator[ConnectionPool]:
    os.environ.setdefault("AIDEVSWARM_PG_HOST", "localhost")
    settings = Settings()
    try:
        pool = open_pool(settings)
    except Exception as exc:
        pytest.skip(f"Postgres unavailable: {exc}")
    yield pool
    close_pool()


@pytest.fixture
def milestone_row(live_pool: ConnectionPool) -> Iterator[UUID]:
    """Insert a throwaway project + milestone, clean up after."""
    pid = uuid4()
    mid = uuid4()
    spec = {
        "title": "sess-test",
        "summary": "smoke",
        "rationale": "smoke",
        "stack": [],
        "tags": [],
        "score": 80,
    }
    mspec = {
        "title": "m",
        "description": "d",
        "acceptance_criteria": [],
        "technical_note": "",
    }
    with live_pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO projects (id, name, spec, state) VALUES (%s, %s, %s, 'queued')",
            (str(pid), f"sess-test-{pid}", Json(spec)),
        )
        cur.execute(
            """
            INSERT INTO milestones (id, project_id, ordinal, title, spec, state)
            VALUES (%s, %s, 0, 'm', %s, 'pending')
            """,
            (str(mid), str(pid), Json(mspec)),
        )
    try:
        yield mid
    finally:
        with live_pool.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM projects WHERE id = %s", (str(pid),))


def test_record_then_latest_for_round_trip(live_pool: ConnectionPool, milestone_row: UUID) -> None:
    repo = PsycopgMilestoneSessionRepo(live_pool)
    repo.record(
        milestone_id=milestone_row,
        role="Developer",
        session_id="sess-alpha",
        cost_usd=0.42,
        turns=7,
    )
    latest = repo.latest_for(milestone_row, "Developer")
    assert latest is not None
    assert latest.session_id == "sess-alpha"
    assert latest.cost_usd == 0.42
    assert latest.turns == 7
    assert latest.role == "Developer"


def test_latest_for_returns_newest_row(live_pool: ConnectionPool, milestone_row: UUID) -> None:
    repo = PsycopgMilestoneSessionRepo(live_pool)
    repo.record(milestone_id=milestone_row, role="Tester", session_id="t1", cost_usd=0.1, turns=2)
    repo.record(milestone_id=milestone_row, role="Tester", session_id="t2", cost_usd=0.2, turns=3)
    latest = repo.latest_for(milestone_row, "Tester")
    assert latest is not None
    # Newest is whichever row has the greatest (finished_at, id); both
    # share now() to subsecond precision so the (finished_at DESC, id
    # DESC) tiebreak should pick t2.
    assert latest.session_id == "t2"


def test_latest_for_missing_returns_none(live_pool: ConnectionPool) -> None:
    repo = PsycopgMilestoneSessionRepo(live_pool)
    assert repo.latest_for(uuid4(), "Developer") is None
