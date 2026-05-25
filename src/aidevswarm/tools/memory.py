"""pgvector-backed idea dedup memory.

The Ideation crew should never re-pitch a project the swarm has already
built. We embed every shipped project's spec, store the vector in
``idea_embeddings``, and dedup new candidates by cosine similarity.

Uses the process-wide ``psycopg_pool.ConnectionPool`` (Phase 1+).
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from psycopg_pool import ConnectionPool


def _vector_literal(values: Sequence[float]) -> str:
    """pgvector wire format is the textual ``[v1,v2,...]`` literal."""
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


class PgvectorMemory:
    """Concrete :class:`aidevswarm.tools.protocols.MemoryStore`."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def remember(self, project_id: UUID, embedding: Sequence[float]) -> None:
        if not embedding:
            raise ValueError("embedding must be non-empty")
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO idea_embeddings (project_id, embedding)
                VALUES (%s, %s::vector)
                ON CONFLICT (project_id) DO UPDATE
                  SET embedding = EXCLUDED.embedding
                """,
                (str(project_id), _vector_literal(embedding)),
            )

    def is_duplicate(self, embedding: Sequence[float], *, threshold: float = 0.92) -> bool:
        """True when any stored embedding is within ``1 - threshold`` cosine distance."""
        if not embedding:
            raise ValueError("embedding must be non-empty")
        distance_cap = 1.0 - threshold
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM idea_embeddings
                WHERE embedding <=> %s::vector < %s
                LIMIT 1
                """,
                (_vector_literal(embedding), distance_cap),
            )
            row = cur.fetchone()
            return row is not None
