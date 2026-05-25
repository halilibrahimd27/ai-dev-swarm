"""Database access layer.

Business code depends on :mod:`aidevswarm.db.protocols`; the psycopg3
implementations live in :mod:`aidevswarm.db.repositories`. The
``open_connection`` context manager lives in :mod:`aidevswarm.db.connection`.
"""

from aidevswarm.db.connection import open_connection
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
    "open_connection",
]
