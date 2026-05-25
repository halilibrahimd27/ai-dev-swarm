"""In-memory fakes that satisfy the Protocols the orchestrator depends on.

These let tests drive the entire :class:`Tick` state machine without
Postgres, Redis, Docker, GitHub, or a real LLM.

All fakes are intentionally minimal — they implement only the slice of
each Protocol the Phase 0 orchestrator actually exercises.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from aidevswarm._time import utc_now
from aidevswarm.schemas import (
    Milestone,
    MilestoneBuildResult,
    MilestoneGraph,
    MilestoneSpec,
    MilestoneState,
    Project,
    ProjectSpec,
    ProjectState,
    ScoredIdea,
)
from aidevswarm.tools import Sandbox, SandboxRun, Workspace

# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------


@dataclass
class InMemoryProjectRepo:
    """Satisfies :class:`aidevswarm.db.protocols.ProjectRepo`."""

    rows: dict[UUID, Project] = field(default_factory=dict)

    def create(self, project: Project) -> Project:
        self.rows[project.id] = project
        return project

    def get(self, project_id: UUID) -> Project | None:
        return self.rows.get(project_id)

    def get_active(self) -> Project | None:
        active_states = {
            ProjectState.PLANNING,
            ProjectState.AWAITING_APPROVAL,
            ProjectState.BUILDING,
            ProjectState.INTEGRATION,
        }
        for project in self.rows.values():
            if project.state in active_states:
                return project
        return None

    def list_by_state(self, state: ProjectState) -> list[Project]:
        return [p for p in self.rows.values() if p.state == state]

    def update_state(self, project_id: UUID, new_state: ProjectState) -> Project:
        existing = self.rows[project_id]
        updated = existing.model_copy(update={"state": new_state, "updated_at": utc_now()})
        self.rows[project_id] = updated
        return updated

    def set_github_repo(self, project_id: UUID, repo_url: str) -> None:
        existing = self.rows[project_id]
        self.rows[project_id] = existing.model_copy(update={"github_repo": repo_url})


@dataclass
class InMemoryMilestoneRepo:
    """Satisfies :class:`aidevswarm.db.protocols.MilestoneRepo`."""

    rows: dict[UUID, Milestone] = field(default_factory=dict)

    def create_many(self, project_id: UUID, specs: list[MilestoneSpec]) -> list[Milestone]:
        created: list[Milestone] = []
        for ordinal, spec in enumerate(specs):
            ms = Milestone(
                id=uuid4(),
                project_id=project_id,
                ordinal=ordinal,
                title=spec.title,
                spec=spec,
            )
            self.rows[ms.id] = ms
            created.append(ms)
        return created

    def list_for_project(self, project_id: UUID) -> list[Milestone]:
        return sorted(
            (m for m in self.rows.values() if m.project_id == project_id),
            key=lambda m: m.ordinal,
        )

    def next_pending(self, project_id: UUID) -> Milestone | None:
        pending_states = {MilestoneState.PENDING, MilestoneState.FAILED}
        candidates = sorted(
            (
                m
                for m in self.rows.values()
                if m.project_id == project_id and m.state in pending_states
            ),
            key=lambda m: m.ordinal,
        )
        return candidates[0] if candidates else None

    def update_state(self, milestone_id: UUID, new_state: MilestoneState) -> Milestone:
        existing = self.rows[milestone_id]
        updated = existing.model_copy(update={"state": new_state, "updated_at": utc_now()})
        self.rows[milestone_id] = updated
        return updated

    def record_attempt(
        self,
        milestone_id: UUID,
        *,
        success: bool,
        commit_hash: str | None,
    ) -> Milestone:
        existing = self.rows[milestone_id]
        new_state = MilestoneState.DONE if success else MilestoneState.FAILED
        new_retry = existing.retry_count + (0 if success else 1)
        updated = existing.model_copy(
            update={
                "state": new_state,
                "retry_count": new_retry,
                "commit_hash": commit_hash if commit_hash else existing.commit_hash,
                "updated_at": utc_now(),
            }
        )
        self.rows[milestone_id] = updated
        return updated


@dataclass
class InMemoryTokenLogRepo:
    """Satisfies :class:`aidevswarm.db.protocols.TokenLogRepo`."""

    records: list[dict[str, Any]] = field(default_factory=list)

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
        self.records.append(
            {
                "project_id": project_id,
                "milestone_id": milestone_id,
                "role": role,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost_usd,
            }
        )

    def daily_total_tokens(self) -> int:
        return sum(r["input_tokens"] + r["output_tokens"] for r in self.records)

    def milestone_total_tokens(self, milestone_id: UUID) -> int:
        return sum(
            r["input_tokens"] + r["output_tokens"]
            for r in self.records
            if r["milestone_id"] == milestone_id
        )


# ---------------------------------------------------------------------------
# Crews
# ---------------------------------------------------------------------------


@dataclass
class FakeIdeationCrew:
    """Returns a canned scored idea every run."""

    ideas: list[ScoredIdea] = field(default_factory=list)

    def run(self) -> list[ScoredIdea]:
        return list(self.ideas)


@dataclass
class FakePlanningCrew:
    """Returns a pre-baked milestone graph."""

    graph: MilestoneGraph | None = None

    def run(self, spec: ProjectSpec) -> MilestoneGraph:
        del spec
        if self.graph is None:
            raise AssertionError("FakePlanningCrew was called without a graph")
        return self.graph


@dataclass
class FakeBuildCrew:
    """Marks every milestone as a successful build and writes one file."""

    succeed: bool = True
    calls: int = 0

    def run(
        self,
        *,
        milestone: Milestone,
        workspace: Workspace,
        sandbox: Sandbox,
    ) -> MilestoneBuildResult:
        del sandbox  # we don't gate via sandbox in fakes — the real crew does
        self.calls += 1
        if not self.succeed:
            return MilestoneBuildResult(success=False, summary="forced fail", failure_reason="fake")
        # Write a tiny file so the workspace becomes dirty -> tick commits.
        (workspace.root / f"{milestone.title.replace(' ', '_')}.txt").write_text(
            f"milestone: {milestone.title}\n", encoding="utf-8"
        )
        return MilestoneBuildResult(success=True, summary="ok", tokens_used=42)


# ---------------------------------------------------------------------------
# Tool stubs
# ---------------------------------------------------------------------------


@dataclass
class FakeSandbox:
    """Satisfies :class:`aidevswarm.tools.protocols.Sandbox`."""

    pass_through: bool = True

    def run_ci(self, workspace_dir: str) -> SandboxRun:
        del workspace_dir
        if self.pass_through:
            return SandboxRun(passed=True, stdout="ok", stderr="", exit_code=0)
        return SandboxRun(passed=False, stdout="", stderr="fail", exit_code=1)


@dataclass
class RecordingTelegram:
    """One-way notifier that just keeps a list of every message sent."""

    sent: list[str] = field(default_factory=list)

    def send(self, message: str) -> None:
        self.sent.append(message)


@dataclass
class FakeGitHub:
    """Records PR opens; returns a deterministic URL."""

    calls: list[dict[str, str]] = field(default_factory=list)

    def open_pr(self, *, repo_url: str, branch: str, title: str, body: str) -> str:
        self.calls.append({"repo_url": repo_url, "branch": branch, "title": title, "body": body})
        return f"https://example.invalid/pr/{len(self.calls)}"


@dataclass
class FakeMemoryStore:
    """No-op pgvector substitute."""

    seen: list[UUID] = field(default_factory=list)

    def remember(self, project_id: UUID, embedding: Sequence[float]) -> None:
        del embedding
        self.seen.append(project_id)

    def is_duplicate(self, embedding: Sequence[float], *, threshold: float = 0.92) -> bool:
        del embedding, threshold
        return False


@dataclass
class FakeSteeringRepo:
    """In-memory :class:`aidevswarm.steering.protocols.SteeringRepo`.

    Mirrors the production semantics: ``pull_unconsumed`` marks the
    notes consumed in-place and returns each note exactly once for a
    given (project, role) pair. Two pulls for the same role return
    nothing on the second call.
    """

    rows: list[dict[str, object]] = field(default_factory=list)
    _next_id: int = 1

    def add_note(self, project_id: UUID, body: str, *, author: str = "human") -> int:
        note_id = self._next_id
        self._next_id += 1
        self.rows.append(
            {
                "id": note_id,
                "project_id": project_id,
                "body": body,
                "author": author,
                "consumed_at": None,
                "consumed_by": None,
            }
        )
        return note_id

    def pull_unconsumed(self, project_id: UUID, role: str) -> list[str]:
        bodies: list[str] = []
        for row in self.rows:
            if row["project_id"] == project_id and row["consumed_at"] is None:
                bodies.append(str(row["body"]))
                row["consumed_at"] = "now"
                row["consumed_by"] = role
        return bodies
