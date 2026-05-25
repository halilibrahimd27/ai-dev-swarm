"""psycopg3-backed repository implementations (pool-based).

Each repo takes a :class:`psycopg_pool.ConnectionPool` and acquires
connections via ``with self._pool.connection() as conn:``. Tests
substitute in-memory fakes in ``tests/fakes.py`` that satisfy the same
:mod:`aidevswarm.db.protocols` interfaces.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

from aidevswarm._time import utc_now
from aidevswarm.schemas import (
    Milestone,
    MilestoneSpec,
    MilestoneState,
    Project,
    ProjectSpec,
    ProjectState,
)


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

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def create(self, project: Project) -> Project:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
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
            row = cur.fetchone()
            assert row is not None, "INSERT ... RETURNING * always yields a row"
            return _project_from_row(row)

    def get(self, project_id: UUID) -> Project | None:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM projects WHERE id = %s", (str(project_id),))
            row = cur.fetchone()
            return _project_from_row(row) if row else None

    def get_active(self) -> Project | None:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
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
            return _project_from_row(row) if row else None

    def list_by_state(self, state: ProjectState) -> list[Project]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM projects WHERE state = %s", (state.value,))
            return [_project_from_row(r) for r in cur.fetchall()]

    def update_state(self, project_id: UUID, new_state: ProjectState) -> Project:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
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
            return _project_from_row(row)

    def set_github_repo(self, project_id: UUID, repo_url: str) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE projects SET github_repo = %s, updated_at = %s WHERE id = %s",
                (repo_url, utc_now(), str(project_id)),
            )


class PsycopgMilestoneRepo:
    """Concrete :class:`aidevswarm.db.protocols.MilestoneRepo`."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def create_many(self, project_id: UUID, specs: list[MilestoneSpec]) -> list[Milestone]:
        rows: list[dict[str, Any]] = []
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
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
                row = cur.fetchone()
                assert row is not None, "INSERT ... RETURNING * always yields a row"
                rows.append(row)
        return [_milestone_from_row(r) for r in rows]

    def list_for_project(self, project_id: UUID) -> list[Milestone]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT * FROM milestones WHERE project_id = %s ORDER BY ordinal",
                (str(project_id),),
            )
            return [_milestone_from_row(r) for r in cur.fetchall()]

    def next_pending(self, project_id: UUID) -> Milestone | None:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
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
            return _milestone_from_row(row) if row else None

    def update_state(self, milestone_id: UUID, new_state: MilestoneState) -> Milestone:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
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
            return _milestone_from_row(row)

    def record_attempt(
        self,
        milestone_id: UUID,
        *,
        success: bool,
        commit_hash: str | None,
    ) -> Milestone:
        new_state = MilestoneState.DONE if success else MilestoneState.FAILED
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
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
            return _milestone_from_row(row)


class PsycopgTokenLogRepo:
    """Concrete :class:`aidevswarm.db.protocols.TokenLogRepo`."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

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
        with self._pool.connection() as conn, conn.cursor() as cur:
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

    def daily_total_tokens(self) -> int:
        with self._pool.connection() as conn, conn.cursor() as cur:
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
        with self._pool.connection() as conn, conn.cursor() as cur:
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
