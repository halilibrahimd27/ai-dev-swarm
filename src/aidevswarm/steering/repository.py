"""psycopg3-backed :class:`SteeringRepo` implementation.

``pull_unconsumed`` is the contract that has to be right: every note must
be delivered to each addressed role exactly once. Delivery is tracked
per-(note, role) in ``steering_note_deliveries``, so a broadcast note
(``target_role IS NULL``) reaches EACH role once and a targeted note
reaches only its role. The "claim" is the delivery-row INSERT itself:
``ON CONFLICT (note_id, role) DO NOTHING ... RETURNING note_id`` returns
only the rows THIS call created, so two concurrent pulls of the same role
never both return the same note (the PK conflict arbitrates) — exactly the
"deliver once per role" semantic we want, without row locks.
"""

from __future__ import annotations

from uuid import UUID

from psycopg_pool import ConnectionPool


class PsycopgSteeringRepo:
    """Concrete :class:`aidevswarm.steering.protocols.SteeringRepo`."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def add_note(
        self,
        project_id: UUID,
        body: str,
        *,
        author: str = "human",
        target_role: str | None = None,
    ) -> int:
        if not body.strip():
            raise ValueError("body must be non-empty")
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO steering_notes (project_id, body, author, target_role)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (str(project_id), body, author, target_role),
            )
            row = cur.fetchone()
            assert row is not None, "INSERT ... RETURNING always yields a row"
            return int(row[0])

    def pull_unconsumed(self, project_id: UUID, role: str) -> list[str]:
        if not role:
            raise ValueError("role must be non-empty")
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            # Candidate notes addressed to this role and not yet delivered to it.
            cur.execute(
                """
                SELECT n.id, n.body
                  FROM steering_notes n
                 WHERE n.project_id = %s
                   AND (n.target_role IS NULL OR n.target_role = %s)
                   AND NOT EXISTS (
                       SELECT 1 FROM steering_note_deliveries d
                        WHERE d.note_id = n.id AND d.role = %s
                   )
                 ORDER BY n.created_at, n.id
                """,
                (str(project_id), role, role),
            )
            rows = cur.fetchall()
            if not rows:
                return []
            body_by_id = {int(r[0]): str(r[1]) for r in rows}
            # Claim by inserting the delivery rows; RETURNING yields only the
            # rows we actually created, so a concurrent same-role pull that
            # selected the same notes hits the PK conflict and returns nothing.
            cur.execute(
                """
                INSERT INTO steering_note_deliveries (note_id, role)
                SELECT unnest(%s::bigint[]), %s
                ON CONFLICT (note_id, role) DO NOTHING
                RETURNING note_id
                """,
                (list(body_by_id), role),
            )
            claimed = {int(r[0]) for r in cur.fetchall()}
            # Preserve the SELECT's chronological order.
            return [body_by_id[nid] for nid in body_by_id if nid in claimed]
