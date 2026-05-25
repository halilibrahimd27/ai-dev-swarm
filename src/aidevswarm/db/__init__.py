"""Database access layer (pool-based since Phase 1).

Business code depends on :mod:`aidevswarm.db.protocols`; the psycopg3
implementations live in :mod:`aidevswarm.db.repositories`. The
process-wide :class:`psycopg_pool.ConnectionPool` is owned by
:mod:`aidevswarm.db.pool` — call :func:`open_pool` once at startup and
:func:`close_pool` on shutdown.
"""

from aidevswarm.db.pool import close_pool, get_pool, open_pool
from aidevswarm.db.protocols import MilestoneRepo, ProjectRepo, TokenLogRepo
from aidevswarm.db.repositories import (
    PsycopgMilestoneRepo,
    PsycopgProjectRepo,
    PsycopgTokenLogRepo,
)

__all__ = [
    "MilestoneRepo",
    "ProjectRepo",
    "PsycopgMilestoneRepo",
    "PsycopgProjectRepo",
    "PsycopgTokenLogRepo",
    "TokenLogRepo",
    "close_pool",
    "get_pool",
    "open_pool",
]
