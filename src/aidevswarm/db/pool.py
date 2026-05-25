"""Process-wide psycopg3 ConnectionPool.

Phase 1 replaces the per-call ``open_connection`` helper with a
long-lived pool. Repositories acquire connections via
``with pool.connection() as conn:`` and release them on block exit.

There is exactly one pool per process. It is opened explicitly from the
orchestrator's startup hook and closed on shutdown — no implicit
construction during import or first use.
"""

from __future__ import annotations

import threading

from psycopg_pool import ConnectionPool

from aidevswarm.logging_config import get_logger
from aidevswarm.settings import Settings

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()


def open_pool(settings: Settings) -> ConnectionPool:
    """Open the process-wide pool. Idempotent (subsequent calls reuse)."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        log = get_logger(__name__)
        log.info(
            "pool.open",
            min_size=settings.pg_pool_min,
            max_size=settings.pg_pool_max,
            timeout=settings.pg_pool_timeout,
            max_lifetime=settings.pg_pool_max_lifetime,
        )
        new_pool = ConnectionPool(
            conninfo=settings.pg_dsn,
            min_size=settings.pg_pool_min,
            max_size=settings.pg_pool_max,
            timeout=float(settings.pg_pool_timeout),
            max_lifetime=float(settings.pg_pool_max_lifetime),
            open=True,
        )
        # Wait for the pool to actually have at least one usable
        # connection; surface a clear error early if Postgres is down.
        # If wait() raises, tear down the half-open pool BEFORE bubbling
        # so a retry from a later fixture / startup gets a clean slate.
        try:
            new_pool.wait(timeout=float(settings.pg_pool_timeout))
        except Exception:
            new_pool.close()
            raise
        _pool = new_pool
        return _pool


def get_pool() -> ConnectionPool:
    """Return the open pool. Raises if :func:`open_pool` was not called."""
    if _pool is None:
        raise RuntimeError("Connection pool not opened — call open_pool(settings) first")
    return _pool


def close_pool() -> None:
    """Close the pool and clear the module-level cache."""
    global _pool
    with _pool_lock:
        if _pool is None:
            return
        get_logger(__name__).info("pool.close")
        _pool.close()
        _pool = None


__all__ = ["close_pool", "get_pool", "open_pool"]
