"""50-thread ``SELECT 1`` smoke test for the psycopg3 connection pool.

Skips automatically when Postgres is not reachable, so the gauntlet
stays green in CI without Docker.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from psycopg_pool import ConnectionPool

# ``live_pool`` comes from tests/integration/conftest.py — it opens the
# process-wide pool against an isolated ``<base>_test`` database and skips
# the suite if Postgres is unreachable.

pytestmark = pytest.mark.integration


def _select_one(pool: ConnectionPool) -> int:
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        row = cur.fetchone()
        assert row is not None
        return int(row[0])


def test_50_concurrent_select_1_under_2_seconds(live_pool: ConnectionPool) -> None:
    """All 50 worker threads must finish their SELECT 1 within 2 seconds."""
    n = 50
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=n) as executor:
        futures = [executor.submit(_select_one, live_pool) for _ in range(n)]
        results = [f.result(timeout=2.0) for f in as_completed(futures)]
    elapsed = time.perf_counter() - start

    assert len(results) == n
    assert all(r == 1 for r in results)
    assert elapsed < 2.0, f"50 concurrent SELECT 1 took {elapsed:.2f}s, expected <2s"


def test_pool_handoff_recycles_connections(live_pool: ConnectionPool) -> None:
    """A burst of more queries than max_size must still complete."""
    # Two consecutive bursts of 30 each — exceeds the default max_size=20.
    # The pool should hand connections out, take them back, and re-vend
    # them to the next caller without timing out.
    for _ in range(2):
        with ThreadPoolExecutor(max_workers=30) as executor:
            futures = [executor.submit(_select_one, live_pool) for _ in range(30)]
            results = [f.result(timeout=5.0) for f in as_completed(futures)]
        assert results == [1] * 30
