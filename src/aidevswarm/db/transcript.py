"""Durable transcript repository + a persisting publisher.

The live transcript (``EventBridge``) is ephemeral — per-connection
queues that a page refresh wipes. :class:`PsycopgTranscriptRepo` writes
every entry to the ``transcript_entries`` table so the web UI can replay
a project's whole conversation on load.

:class:`PersistingTranscriptPublisher` is the glue: it satisfies the
:class:`TranscriptPublisher` protocol, persists each transcript-topic
entry (best-effort — a DB hiccup must never break a build), then forwards
to the live sink (the bridge) unchanged.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

from aidevswarm.logging_config import get_logger
from aidevswarm.observability import TranscriptEntry, TranscriptPublisher


class TranscriptRepo(Protocol):
    """The slice the API + publisher need."""

    def append(self, entry: TranscriptEntry) -> None: ...

    def list_for_project(self, project_id: UUID, *, limit: int = 5000) -> list[TranscriptEntry]: ...


class PsycopgTranscriptRepo:
    """Concrete :class:`TranscriptRepo` (pool-based)."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def append(self, entry: TranscriptEntry) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO transcript_entries
                  (id, project_id, role, kind, body, extra, at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(entry.id),
                    str(entry.project_id) if entry.project_id is not None else None,
                    entry.role,
                    entry.kind,
                    entry.text,
                    Json(entry.extra),
                    entry.at,
                ),
            )

    def list_for_project(self, project_id: UUID, *, limit: int = 5000) -> list[TranscriptEntry]:
        """Return up to ``limit`` most-recent entries in CHRONOLOGICAL order."""
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, role, kind, body, extra, at
                  FROM transcript_entries
                 WHERE project_id = %s
                 ORDER BY seq DESC
                 LIMIT %s
                """,
                (str(project_id), limit),
            )
            rows = cur.fetchall()
        rows.reverse()  # newest-first query → oldest-first for replay
        return [
            TranscriptEntry(
                id=row[0],
                topic="transcript",
                project_id=project_id,
                role=row[1],
                kind=row[2],
                text=row[3],
                extra=row[4] or {},
                at=_as_datetime(row[5]),
            )
            for row in rows
        ]


class PersistingTranscriptPublisher:
    """A :class:`TranscriptPublisher` that durably records before fanning out."""

    def __init__(self, repo: TranscriptRepo, sink: TranscriptPublisher) -> None:
        self._repo = repo
        self._sink = sink
        self._log = get_logger(__name__)

    def publish(self, entry: TranscriptEntry) -> None:
        if entry.topic == "transcript":
            try:
                self._repo.append(entry)
            except Exception as exc:  # persistence is best-effort
                self._log.warning("transcript.persist_failed", error=str(exc))
        self._sink.publish(entry)


def _as_datetime(value: object) -> datetime:
    assert isinstance(value, datetime), f"expected datetime, got {type(value).__name__}"
    return value


__all__ = ["PersistingTranscriptPublisher", "PsycopgTranscriptRepo", "TranscriptRepo"]
