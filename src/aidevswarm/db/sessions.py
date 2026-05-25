"""Milestone-session repository.

Persists every Claude Agent SDK ``ResultMessage`` (session_id,
cost_usd, num_turns) so a retry of the same milestone can resume the
existing SDK conversation rather than restarting.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from psycopg_pool import ConnectionPool

from aidevswarm.schemas import MilestoneSession


class MilestoneSessionRepo(Protocol):
    """CRUD slice the SDK tools need."""

    def record(
        self,
        *,
        milestone_id: UUID,
        role: str,
        session_id: str,
        cost_usd: float,
        turns: int,
    ) -> MilestoneSession: ...

    def latest_for(self, milestone_id: UUID, role: str) -> MilestoneSession | None: ...


class PsycopgMilestoneSessionRepo:
    """Concrete :class:`MilestoneSessionRepo` (pool-based)."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def record(
        self,
        *,
        milestone_id: UUID,
        role: str,
        session_id: str,
        cost_usd: float,
        turns: int,
    ) -> MilestoneSession:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO milestone_sessions
                  (milestone_id, role, session_id, cost_usd, turns)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, finished_at
                """,
                (str(milestone_id), role, session_id, cost_usd, turns),
            )
            row = cur.fetchone()
            assert row is not None, "INSERT ... RETURNING always yields a row"
            row_id, finished_at = int(row[0]), row[1]
        return MilestoneSession(
            id=row_id,
            milestone_id=milestone_id,
            role=role,
            session_id=session_id,
            cost_usd=cost_usd,
            turns=turns,
            finished_at=_as_datetime(finished_at),
        )

    def latest_for(self, milestone_id: UUID, role: str) -> MilestoneSession | None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, session_id, cost_usd, turns, finished_at
                  FROM milestone_sessions
                 WHERE milestone_id = %s AND role = %s
                 ORDER BY finished_at DESC, id DESC
                 LIMIT 1
                """,
                (str(milestone_id), role),
            )
            row = cur.fetchone()
            if row is None:
                return None
        return MilestoneSession(
            id=int(row[0]),
            milestone_id=milestone_id,
            role=role,
            session_id=str(row[1]),
            cost_usd=float(row[2]),
            turns=int(row[3]),
            finished_at=_as_datetime(row[4]),
        )


def _as_datetime(value: object) -> datetime:
    """Narrow the psycopg row value to a datetime for mypy."""
    assert isinstance(value, datetime), f"expected datetime, got {type(value).__name__}"
    return value
