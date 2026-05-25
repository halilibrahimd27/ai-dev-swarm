"""psycopg3 connection helper.

Phase 0 uses a single short-lived connection per call (no pool). Phase 1
swaps this for ``psycopg_pool.ConnectionPool``; callers should depend on
the :func:`open_connection` context manager rather than the underlying
connection object.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from aidevswarm.settings import Settings


@contextmanager
def open_connection(settings: Settings) -> Iterator[psycopg.Connection]:
    """Yield a psycopg3 connection and close it deterministically.

    The connection is opened in autocommit=False mode; callers must
    either commit explicitly or wrap their work in :meth:`Connection.transaction`.
    """
    conn = psycopg.connect(settings.pg_dsn, autocommit=False)
    try:
        yield conn
    finally:
        conn.close()
