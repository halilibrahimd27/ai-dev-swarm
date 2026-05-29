"""Shared fixtures for the integration suite — isolated test database.

Integration tests used to open a pool against the operator's LIVE
``aidevswarm`` database (each test file defined its own ``live_pool``
that called ``open_pool(Settings())``). That was wrong on two counts:

  * tests WROTE to the live DB the running swarm reads from, and
  * a real in-flight project polluted ``get_active``-style assertions
    (the swarm's project, not the test's, came back "active").

This module provides ONE ``live_pool`` fixture that talks to the SAME
Postgres SERVER the operator runs but to a SEPARATE database
(``<base>_test``). The database is created on demand, ``init.sql`` +
``alembic upgrade head`` bring its schema up to the production shape, and
it is truncated once at session start so every run begins clean. The
live database is never touched. If Postgres is unreachable the whole
suite skips, exactly as before.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from aidevswarm.db.pool import close_pool, open_pool
from aidevswarm.settings import Settings

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INIT_SQL = _REPO_ROOT / "docker" / "init.sql"
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"

# Loopback by default so the suite runs from the host against the
# docker-published 5432. Overridable for unusual setups.
_HOST = os.environ.get("AIDEVSWARM_TEST_PG_HOST", "localhost")
_BASE_DB = os.environ.get("AIDEVSWARM_TEST_BASE_DB", "aidevswarm")
_TEST_DB = os.environ.get("AIDEVSWARM_TEST_PG_DB", f"{_BASE_DB}_test")

# Truncated at session start for a clean slate. ``projects`` cascades to
# milestones / token_log / steering_notes / milestone_sessions;
# idea_evaluations only SET-NULLs its project_id, so it is truncated
# explicitly.
_TRUNCATE_SQL = "TRUNCATE projects, idea_evaluations RESTART IDENTITY CASCADE"


def _settings_for(db: str) -> Settings:
    """Settings pointed at ``db`` on the test server.

    Credentials (user/password/port) come from the operator's ``.env`` so
    we authenticate against the same running Postgres; host + database are
    forced to loopback + the *test* database so we never touch the
    configured live one.

    NB: ``pg_host`` / ``postgres_db`` carry pydantic ``validation_alias``es
    (``AIDEVSWARM_PG_HOST`` / ``POSTGRES_DB``), so init kwargs by field NAME
    are ignored. ``model_copy(update=...)`` sets the fields by name after
    construction, which is the reliable override. ``_env_file`` is pinned to
    the repo ``.env`` so credential loading doesn't depend on the cwd (the
    suite's autouse fixture chdir's per test).
    """
    base = Settings(_env_file=str(_REPO_ROOT / ".env"))
    return base.model_copy(update={"pg_host": _HOST, "postgres_db": db})


def _ensure_database() -> None:
    """``CREATE DATABASE <test>`` if it does not already exist."""
    with psycopg.connect(_settings_for(_BASE_DB).pg_dsn, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (_TEST_DB,)
        ).fetchone()
        if exists is None:
            # Identifier can't be parameterised; _TEST_DB is derived from
            # our own config (not user input) and double-quoted.
            conn.execute(f'CREATE DATABASE "{_TEST_DB}"')


def _apply_init_sql() -> None:
    """Apply the production base schema (idempotent ``IF NOT EXISTS`` DDL)."""
    sql = _INIT_SQL.read_text("utf-8")
    with psycopg.connect(_settings_for(_TEST_DB).pg_dsn, autocommit=True) as conn:
        conn.execute(sql)


def _apply_migrations() -> None:
    """Bring the test DB to ``head`` via the real Alembic migrations.

    ``alembic/env.py`` builds its URL from a bare ``Settings()``, so the
    connection target is forced through env vars (which outrank ``.env``)
    for the duration of the upgrade, then restored.
    """
    from alembic import command
    from alembic.config import Config

    test = _settings_for(_TEST_DB)
    overrides = {
        "AIDEVSWARM_PG_HOST": test.pg_host,
        "AIDEVSWARM_PG_PORT": str(test.pg_port),
        "POSTGRES_DB": test.postgres_db,
        "POSTGRES_USER": test.postgres_user,
        "POSTGRES_PASSWORD": test.postgres_password.get_secret_value(),
    }
    saved = {k: os.environ.get(k) for k in overrides}
    os.environ.update(overrides)
    try:
        cfg = Config(str(_ALEMBIC_INI))
        cfg.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
        command.upgrade(cfg, "head")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _truncate() -> None:
    with psycopg.connect(_settings_for(_TEST_DB).pg_dsn, autocommit=True) as conn:
        conn.execute(_TRUNCATE_SQL)


@pytest.fixture(scope="session")
def _test_db_settings() -> Settings:
    """Provision the isolated test database once per session.

    Skips the whole integration suite if Postgres is unreachable.
    """
    try:
        _ensure_database()
        _apply_init_sql()
        _apply_migrations()
        _truncate()
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres unavailable: {exc}")
    return _settings_for(_TEST_DB)


@pytest.fixture(scope="module")
def live_pool(_test_db_settings: Settings) -> Iterator[ConnectionPool]:
    """Process-wide pool bound to the isolated test database.

    Closes any stale global pool first so we never inherit one pointed at
    a different database from an earlier (non-integration) test.
    """
    close_pool()
    pool = open_pool(_test_db_settings)
    try:
        yield pool
    finally:
        close_pool()
