"""psycopg3-backed :class:`SteeringRepo` implementation.

``pull_unconsumed`` is the contract that has to be right: every note
must be delivered to a role exactly once. We lock the candidate rows
with ``SELECT ... FOR UPDATE`` inside a transaction, then UPDATE them
with ``consumed_at = now()`` and the consuming role. Two concurrent
runs of two different roles will each see their own copies because
they hold the lock for the duration; two runs of the *same* role race
on the SELECT, but the loser sees an empty result-set after the
winner's UPDATE commits — which is exactly the "deliver once" semantic
we want.
"""

from __future__ import annotations

from uuid import UUID

from psycopg_pool import ConnectionPool


class PsycopgSteeringRepo:
    """Concrete :class:`aidevswarm.steering.protocols.SteeringRepo`."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def add_note(self, project_id: UUID, body: str, *, author: str = "human") -> int:
        if not body.strip():
            raise ValueError("body must be non-empty")
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO steering_notes (project_id, body, author)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (str(project_id), body, author),
            )
            row = cur.fetchone()
            assert row is not None, "INSERT ... RETURNING always yields a row"
            return int(row[0])

    def pull_unconsumed(self, project_id: UUID, role: str) -> list[str]:
        if not role:
            raise ValueError("role must be non-empty")
        with self._pool.connection() as conn:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, body FROM steering_notes
                     WHERE project_id = %s AND consumed_at IS NULL
                     ORDER BY created_at, id
                     FOR UPDATE SKIP LOCKED
                    """,
                    (str(project_id),),
                )
                rows = cur.fetchall()
                if not rows:
                    return []
                ids = [int(r[0]) for r in rows]
                bodies = [str(r[1]) for r in rows]
                cur.execute(
                    """
                    UPDATE steering_notes
                       SET consumed_at = now(), consumed_by = %s
                     WHERE id = ANY(%s)
                    """,
                    (role, ids),
                )
            return bodies
