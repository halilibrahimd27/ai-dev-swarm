"""psycopg3-backed repository implementations.

These are the production impls; tests substitute in-memory fakes defined
in ``tests/fakes.py`` that satisfy the same :mod:`aidevswarm.db.protocols`
interfaces.

Phase 1 replaces the per-call ``open_connection`` with a long-lived
``psycopg_pool.ConnectionPool``; the public method shapes stay the same.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Json

from aidevswarm._time import utc_now
from aidevswarm.db.connection import open_connection
from aidevswarm.schemas import (
    Milestone,
    MilestoneSpec,
    MilestoneState,
    Project,
    ProjectSpec,
    ProjectState,
)
from aidevswarm.settings import Settings


def _project_from_row(row: dict[str, Any]) -> Project:
    return Project(
        id=row["id"],
        name=row["name"],
        spec=ProjectSpec.model_validate(row["spec"]),
        state=ProjectState(row["state"]),
        github_repo=row["github_repo"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _milestone_from_row(row: dict[str, Any]) -> Milestone:
    return Milestone(
        id=row["id"],
        project_id=row["project_id"],
        ordinal=row["ordinal"],
        title=row["title"],
        spec=MilestoneSpec.model_validate(row["spec"]),
        state=MilestoneState(row["state"]),
        retry_count=row["retry_count"],
        commit_hash=row["commit_hash"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class PsycopgProjectRepo:
    """Concrete :class:`aidevswarm.db.protocols.ProjectRepo`."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _conn(self) -> Any:
        return open_connection(self._settings)

    def create(self, project: Project) -> Project:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO projects (id, name, spec, state, github_repo,
                                      created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    str(project.id),
                    project.name,
                    Json(project.spec.model_dump()),
                    project.state.value,
                    project.github_repo,
                    project.created_at,
                    project.updated_at,
                ),
            )
            row = cast(dict[str, Any], cur.fetchone())
            conn.commit()
            return _project_from_row(row)

    def get(self, project_id: UUID) -> Project | None:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM projects WHERE id = %s", (str(project_id),))
            row = cur.fetchone()
            return _project_from_row(cast(dict[str, Any], row)) if row else None

    def get_active(self) -> Project | None:
        """Return the project currently in a non-terminal, non-queued state."""
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT * FROM projects
                WHERE state IN ('planning', 'awaiting_approval', 'building',
                                'integration')
                ORDER BY updated_at ASC
                LIMIT 1
                """
            )
            row = cur.fetchone()
            return _project_from_row(cast(dict[str, Any], row)) if row else None

    def list_by_state(self, state: ProjectState) -> list[Project]:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM projects WHERE state = %s", (state.value,))
            return [_project_from_row(cast(dict[str, Any], r)) for r in cur.fetchall()]

    def update_state(self, project_id: UUID, new_state: ProjectState) -> Project:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                UPDATE projects SET state = %s, updated_at = %s
                WHERE id = %s
                RETURNING *
                """,
                (new_state.value, utc_now(), str(project_id)),
            )
            row = cur.fetchone()
            if row is None:
                raise LookupError(f"project {project_id} not found")
            conn.commit()
            return _project_from_row(cast(dict[str, Any], row))

    def set_github_repo(self, project_id: UUID, repo_url: str) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE projects SET github_repo = %s, updated_at = %s WHERE id = %s",
                (repo_url, utc_now(), str(project_id)),
            )
            conn.commit()


class PsycopgMilestoneRepo:
    """Concrete :class:`aidevswarm.db.protocols.MilestoneRepo`."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _conn(self) -> Any:
        return open_connection(self._settings)

    def create_many(self, project_id: UUID, specs: list[MilestoneSpec]) -> list[Milestone]:
        rows: list[dict[str, Any]] = []
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            for ordinal, spec in enumerate(specs):
                cur.execute(
                    """
                    INSERT INTO milestones (project_id, ordinal, title, spec, state)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        str(project_id),
                        ordinal,
                        spec.title,
                        Json(spec.model_dump()),
                        MilestoneState.PENDING.value,
                    ),
                )
                rows.append(cast(dict[str, Any], cur.fetchone()))
            conn.commit()
        return [_milestone_from_row(r) for r in rows]

    def list_for_project(self, project_id: UUID) -> list[Milestone]:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT * FROM milestones WHERE project_id = %s ORDER BY ordinal",
                (str(project_id),),
            )
            return [_milestone_from_row(cast(dict[str, Any], r)) for r in cur.fetchall()]

    def next_pending(self, project_id: UUID) -> Milestone | None:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT * FROM milestones
                WHERE project_id = %s AND state IN ('pending', 'failed')
                ORDER BY ordinal
                LIMIT 1
                """,
                (str(project_id),),
            )
            row = cur.fetchone()
            return _milestone_from_row(cast(dict[str, Any], row)) if row else None

    def update_state(self, milestone_id: UUID, new_state: MilestoneState) -> Milestone:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                UPDATE milestones SET state = %s, updated_at = %s
                WHERE id = %s RETURNING *
                """,
                (new_state.value, utc_now(), str(milestone_id)),
            )
            row = cur.fetchone()
            if row is None:
                raise LookupError(f"milestone {milestone_id} not found")
            conn.commit()
            return _milestone_from_row(cast(dict[str, Any], row))

    def record_attempt(
        self,
        milestone_id: UUID,
        *,
        success: bool,
        commit_hash: str | None,
    ) -> Milestone:
        new_state = MilestoneState.DONE if success else MilestoneState.FAILED
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                UPDATE milestones
                   SET state = %s,
                       commit_hash = COALESCE(%s, commit_hash),
                       retry_count = retry_count + CASE WHEN %s THEN 0 ELSE 1 END,
                       updated_at = %s
                 WHERE id = %s
                 RETURNING *
                """,
                (
                    new_state.value,
                    commit_hash,
                    success,
                    utc_now(),
                    str(milestone_id),
                ),
            )
            row = cur.fetchone()
            if row is None:
                raise LookupError(f"milestone {milestone_id} not found")
            conn.commit()
            return _milestone_from_row(cast(dict[str, Any], row))


class PsycopgTokenLogRepo:
    """Concrete :class:`aidevswarm.db.protocols.TokenLogRepo`."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _conn(self) -> Any:
        return open_connection(self._settings)

    def record(
        self,
        *,
        project_id: UUID | None,
        milestone_id: UUID | None,
        role: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO token_log
                  (project_id, milestone_id, role, model,
                   input_tokens, output_tokens, cost_usd)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(project_id) if project_id else None,
                    str(milestone_id) if milestone_id else None,
                    role,
                    model,
                    input_tokens,
                    output_tokens,
                    cost_usd,
                ),
            )
            conn.commit()

    def daily_total_tokens(self) -> int:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(input_tokens + output_tokens), 0)
                FROM token_log
                WHERE created_at::date = (now() AT TIME ZONE 'UTC')::date
                """
            )
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    def milestone_total_tokens(self, milestone_id: UUID) -> int:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(input_tokens + output_tokens), 0)
                FROM token_log
                WHERE milestone_id = %s
                """,
                (str(milestone_id),),
            )
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0


__all__ = [
    "PsycopgMilestoneRepo",
    "PsycopgProjectRepo",
    "PsycopgTokenLogRepo",
]
