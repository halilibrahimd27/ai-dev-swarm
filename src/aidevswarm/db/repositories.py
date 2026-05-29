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
    CriticScores,
    IdeaEvaluation,
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
        status_detail=row.get("status_detail"),
        is_paused=bool(row.get("is_paused", False)),
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
                                'replanning', 'integration')
                ORDER BY updated_at ASC
                LIMIT 1
                """
            )
            row = cur.fetchone()
            return _project_from_row(row) if row else None

    def list_all(self) -> list[Project]:
        """Return every project row, newest first."""
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM projects ORDER BY created_at DESC")
            return [_project_from_row(r) for r in cur.fetchall()]

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

    def set_status_detail(self, project_id: UUID, detail: str | None) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE projects SET status_detail = %s, updated_at = %s WHERE id = %s",
                (detail, utc_now(), str(project_id)),
            )

    def set_paused(self, project_id: UUID, paused: bool) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE projects SET is_paused = %s, updated_at = %s WHERE id = %s",
                (paused, utc_now(), str(project_id)),
            )

    def is_paused(self, project_id: UUID) -> bool:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT is_paused FROM projects WHERE id = %s", (str(project_id),))
            row = cur.fetchone()
            return bool(row[0]) if row is not None else False


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

    def update_spec(self, milestone_id: UUID, patch: dict[str, Any]) -> Milestone:
        """Apply ``patch`` to the milestone's spec (Phase 4 Amend).

        Unknown keys are rejected by ``MilestoneSpec.model_copy(update=)``
        because the schema has ``extra='forbid'``.
        """
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM milestones WHERE id = %s", (str(milestone_id),))
            current = cur.fetchone()
            if current is None:
                raise LookupError(f"milestone {milestone_id} not found")
            existing_spec = MilestoneSpec.model_validate(current["spec"])
            new_spec = existing_spec.model_copy(update=patch)
            cur.execute(
                """
                UPDATE milestones SET spec = %s, updated_at = %s
                WHERE id = %s RETURNING *
                """,
                (Json(new_spec.model_dump()), utc_now(), str(milestone_id)),
            )
            row = cur.fetchone()
            assert row is not None
            return _milestone_from_row(row)

    def replace_with(self, milestone_id: UUID, into: list[MilestoneSpec]) -> list[Milestone]:
        """Replace one milestone with N children (Phase 4 Split).

        Children inherit the project_id + the deleted milestone's
        ordinal as their starting position; later milestones shift
        down by ``len(into) - 1``.
        """
        if len(into) < 2:
            raise ValueError("replace_with requires at least 2 child specs")
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT project_id, ordinal FROM milestones WHERE id = %s",
                (str(milestone_id),),
            )
            row = cur.fetchone()
            if row is None:
                raise LookupError(f"milestone {milestone_id} not found")
            project_id = row["project_id"]
            start_ordinal = int(row["ordinal"])
            shift = len(into) - 1

            # Bump the ordinals of everything after the parent. UPDATE in
            # descending order to avoid uniqueness collisions on
            # (project_id, ordinal).
            cur.execute(
                """
                UPDATE milestones SET ordinal = ordinal + %s, updated_at = %s
                WHERE project_id = %s AND ordinal > %s
                """,
                (shift, utc_now(), str(project_id), start_ordinal),
            )
            # Delete the parent then insert the children at consecutive
            # ordinals starting at `start_ordinal`.
            cur.execute("DELETE FROM milestones WHERE id = %s", (str(milestone_id),))
            inserted: list[dict[str, Any]] = []
            for i, spec in enumerate(into):
                cur.execute(
                    """
                    INSERT INTO milestones (project_id, ordinal, title, spec, state)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        str(project_id),
                        start_ordinal + i,
                        spec.title,
                        Json(spec.model_dump()),
                        MilestoneState.PENDING.value,
                    ),
                )
                child = cur.fetchone()
                assert child is not None
                inserted.append(child)
        return [_milestone_from_row(r) for r in inserted]

    def insert_after(self, milestone_id: UUID, spec: MilestoneSpec) -> Milestone:
        """Insert a milestone immediately after ``milestone_id``."""
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT project_id, ordinal FROM milestones WHERE id = %s",
                (str(milestone_id),),
            )
            row = cur.fetchone()
            if row is None:
                raise LookupError(f"milestone {milestone_id} not found")
            project_id = row["project_id"]
            after_ordinal = int(row["ordinal"])

            cur.execute(
                """
                UPDATE milestones SET ordinal = ordinal + 1, updated_at = %s
                WHERE project_id = %s AND ordinal > %s
                """,
                (utc_now(), str(project_id), after_ordinal),
            )
            cur.execute(
                """
                INSERT INTO milestones (project_id, ordinal, title, spec, state)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    str(project_id),
                    after_ordinal + 1,
                    spec.title,
                    Json(spec.model_dump()),
                    MilestoneState.PENDING.value,
                ),
            )
            inserted = cur.fetchone()
            assert inserted is not None
        return _milestone_from_row(inserted)


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

    def daily_cost_usd(self) -> float:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(cost_usd), 0)
                FROM token_log
                WHERE created_at::date = (now() AT TIME ZONE 'UTC')::date
                """
            )
            row = cur.fetchone()
            return float(row[0]) if row and row[0] is not None else 0.0

    def daily_by_role(self) -> list[tuple[str, int, float]]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT role,
                       COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens,
                       COALESCE(SUM(cost_usd), 0) AS cost
                FROM token_log
                WHERE created_at::date = (now() AT TIME ZONE 'UTC')::date
                GROUP BY role
                ORDER BY cost DESC
                """
            )
            return [(str(r[0]), int(r[1]), float(r[2])) for r in cur.fetchall()]

    def all_time_totals(self) -> tuple[int, float]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(input_tokens + output_tokens), 0),
                       COALESCE(SUM(cost_usd), 0)
                FROM token_log
                """
            )
            row = cur.fetchone()
            if row is None:
                return 0, 0.0
            return int(row[0]), float(row[1])

    def by_project(self) -> list[tuple[UUID, int, float]]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT project_id,
                       COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens,
                       COALESCE(SUM(cost_usd), 0) AS cost
                FROM token_log
                WHERE project_id IS NOT NULL
                GROUP BY project_id
                ORDER BY cost DESC
                """
            )
            return [(r[0], int(r[1]), float(r[2])) for r in cur.fetchall()]


def _idea_eval_from_row(row: dict[str, Any]) -> IdeaEvaluation:
    return IdeaEvaluation(
        id=int(row["id"]),
        round=int(row["round"]),
        title=row["title"],
        summary=row["summary"],
        scores=CriticScores.model_validate(row["scores"]),
        total=int(row["total"]),
        novel=bool(row["novel"]),
        accepted=bool(row["accepted"]),
        rejected_reason=row["rejected_reason"],
        project_id=row["project_id"],
        created_at=row["created_at"],
    )


class PsycopgIdeaEvaluationRepo:
    """Concrete :class:`aidevswarm.db.protocols.IdeaEvaluationRepo`."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def record(self, evaluation: IdeaEvaluation) -> IdeaEvaluation:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO idea_evaluations
                  (round, title, summary, scores, total, novel, accepted,
                   rejected_reason, project_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    evaluation.round,
                    evaluation.title,
                    evaluation.summary,
                    Json(evaluation.scores.model_dump()),
                    evaluation.total,
                    evaluation.novel,
                    evaluation.accepted,
                    evaluation.rejected_reason,
                    str(evaluation.project_id) if evaluation.project_id else None,
                ),
            )
            row = cur.fetchone()
            assert row is not None, "INSERT ... RETURNING * always yields a row"
            return _idea_eval_from_row(row)

    def list_recent(self, limit: int = 50) -> list[IdeaEvaluation]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT * FROM idea_evaluations ORDER BY created_at DESC, id DESC LIMIT %s",
                (limit,),
            )
            return [_idea_eval_from_row(r) for r in cur.fetchall()]


__all__ = [
    "PsycopgIdeaEvaluationRepo",
    "PsycopgMilestoneRepo",
    "PsycopgProjectRepo",
    "PsycopgTokenLogRepo",
]
