"""Integration test for :class:`PsycopgSettingsOverrideRepo`.

``live_pool`` (tests/integration/conftest.py) points at the isolated
test DB, whose schema includes the settings_overrides table (migration
0006). Verifies upsert + get_all round-trip and upsert-on-conflict.
"""

from __future__ import annotations

import pytest
from psycopg_pool import ConnectionPool

from aidevswarm.db.settings_store import PsycopgSettingsOverrideRepo

pytestmark = pytest.mark.integration


def _clean(pool: ConnectionPool) -> None:
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE settings_overrides")


def test_upsert_then_get_all(live_pool: ConnectionPool) -> None:
    _clean(live_pool)
    repo = PsycopgSettingsOverrideRepo(live_pool)
    repo.upsert("daily_token_budget", "500000")
    repo.upsert("require_approval", "false")
    assert repo.get_all() == {
        "daily_token_budget": "500000",
        "require_approval": "false",
    }


def test_upsert_overwrites_on_conflict(live_pool: ConnectionPool) -> None:
    _clean(live_pool)
    repo = PsycopgSettingsOverrideRepo(live_pool)
    repo.upsert("sandbox_mode", "inmemory")
    repo.upsert("sandbox_mode", "docker")  # same key -> overwrite
    assert repo.get_all() == {"sandbox_mode": "docker"}
