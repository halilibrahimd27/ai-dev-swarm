"""pgvector-backed idea dedup memory.

The Ideation crew should never re-pitch a project the swarm has already
built. We embed every shipped project's spec, store the vector in
``idea_embeddings``, and dedup new candidates by cosine similarity.

Phase 0 uses a single-conn psycopg3 path; Phase 1 switches to the pool.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID

from aidevswarm.db.connection import open_connection
from aidevswarm.settings import Settings


def _vector_literal(values: Sequence[float]) -> str:
    """pgvector wire format is the textual ``[v1,v2,...]`` literal."""
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


class PgvectorMemory:
    """Concrete :class:`aidevswarm.tools.protocols.MemoryStore`."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def remember(self, project_id: UUID, embedding: Sequence[float]) -> None:
        if not embedding:
            raise ValueError("embedding must be non-empty")
        with open_connection(self._settings) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO idea_embeddings (project_id, embedding)
                VALUES (%s, %s::vector)
                ON CONFLICT (project_id) DO UPDATE
                  SET embedding = EXCLUDED.embedding
                """,
                (str(project_id), _vector_literal(embedding)),
            )
            conn.commit()

    def is_duplicate(
        self, embedding: Sequence[float], *, threshold: float = 0.92
    ) -> bool:
        """True when any stored embedding is within ``1 - threshold`` cosine distance."""
        if not embedding:
            raise ValueError("embedding must be non-empty")
        distance_cap = 1.0 - threshold
        with open_connection(self._settings) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM idea_embeddings
                WHERE embedding <=> %s::vector < %s
                LIMIT 1
                """,
                (_vector_literal(embedding), distance_cap),
            )
            row = cast(tuple[Any, ...] | None, cur.fetchone())
            return row is not None
