"""Repository ``Protocol``s.

Business code depends on these interfaces, not on the psycopg-backed
implementations, so the orchestrator can be exercised in tests with
in-memory fakes (no Postgres required).
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from aidevswarm.schemas import (
    IdeaEvaluation,
    Milestone,
    MilestoneSpec,
    MilestoneState,
    Project,
    ProjectState,
)


class ProjectRepo(Protocol):
    """CRUD for the ``projects`` table."""

    def create(self, project: Project) -> Project: ...
    def get(self, project_id: UUID) -> Project | None: ...
    def get_active(self) -> Project | None: ...
    def list_all(self) -> list[Project]: ...
    def list_by_state(self, state: ProjectState) -> list[Project]: ...
    def update_state(self, project_id: UUID, new_state: ProjectState) -> Project: ...
    def set_github_repo(self, project_id: UUID, repo_url: str) -> None: ...
    def set_status_detail(self, project_id: UUID, detail: str | None) -> None: ...
    def set_paused(self, project_id: UUID, paused: bool) -> None: ...
    def is_paused(self, project_id: UUID) -> bool: ...


class MilestoneRepo(Protocol):
    """CRUD for the ``milestones`` table."""

    def create_many(self, project_id: UUID, specs: list[MilestoneSpec]) -> list[Milestone]: ...
    def list_for_project(self, project_id: UUID) -> list[Milestone]: ...
    def next_pending(self, project_id: UUID) -> Milestone | None: ...
    def update_state(self, milestone_id: UUID, new_state: MilestoneState) -> Milestone: ...
    def record_attempt(
        self,
        milestone_id: UUID,
        *,
        success: bool,
        commit_hash: str | None,
    ) -> Milestone: ...
    def reset_retry_count(self, project_id: UUID) -> int:
        """Zero ``retry_count`` for the project's non-done milestones (resume)."""

    def update_spec(self, milestone_id: UUID, patch: dict[str, Any]) -> Milestone:
        """Apply a partial patch to the milestone's spec (Phase 4 Amend)."""

    def replace_with(self, milestone_id: UUID, into: list[MilestoneSpec]) -> list[Milestone]:
        """Replace one milestone with N children (Phase 4 Split)."""

    def insert_after(self, milestone_id: UUID, spec: MilestoneSpec) -> Milestone:
        """Insert a new milestone immediately after ``milestone_id``.

        Subsequent milestones have their ordinals bumped by one so the
        scheduler reaches the inserted milestone next."""


class TokenLogRepo(Protocol):
    """Append-only ledger of LLM token usage."""

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
    ) -> None: ...

    def daily_total_tokens(self) -> int: ...
    def milestone_total_tokens(self, milestone_id: UUID) -> int: ...
    def daily_cost_usd(self) -> float:
        """Sum of ``cost_usd`` recorded so far today (UTC)."""

    def daily_by_role(self) -> list[tuple[str, int, float]]:
        """Today's spend grouped by role: ``(role, tokens, cost_usd)`` rows,
        most-expensive first."""

    def all_time_totals(self) -> tuple[int, float]:
        """All-time ``(total_tokens, total_cost_usd)``."""

    def by_project(self) -> list[tuple[UUID, int, float]]:
        """All-time spend grouped by project: ``(project_id, tokens, cost)``
        rows (project-scoped only), most-expensive first."""


class IdeaEvaluationRepo(Protocol):
    """Append + read the Critic's scored-idea verdicts."""

    def record(self, evaluation: IdeaEvaluation) -> IdeaEvaluation: ...
    def list_recent(self, limit: int = 50) -> list[IdeaEvaluation]: ...
