"""Milestone-graph models produced by the Planning crew."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from aidevswarm.schemas.project import MilestoneState


class AcceptanceCriterion(BaseModel):
    """One concrete, testable acceptance condition for a milestone."""

    model_config = ConfigDict(extra="forbid")

    description: str
    verifier: str = Field(
        description="How the build crew should verify this — e.g. 'pytest', 'curl', 'lint'."
    )


class MilestoneSpec(BaseModel):
    """The Planning crew's spec for a single milestone."""

    model_config = ConfigDict(extra="forbid")

    title: str
    description: str
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    technical_note: str = ""


class Milestone(BaseModel):
    """A row in the ``milestones`` table."""

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    ordinal: int = Field(ge=0)
    title: str
    spec: MilestoneSpec
    state: MilestoneState = MilestoneState.PENDING
    retry_count: int = Field(default=0, ge=0)
    commit_hash: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class MilestoneGraph(BaseModel):
    """Ordered list of milestones for a project (no DAG until Phase 4)."""

    model_config = ConfigDict(extra="forbid")

    milestones: list[MilestoneSpec] = Field(default_factory=list, min_length=1)


class MilestoneBuildResult(BaseModel):
    """What the Build crew returns to the orchestrator per milestone."""

    model_config = ConfigDict(extra="forbid")

    success: bool
    commit_hash: str | None = None
    summary: str
    artifacts: list[str] = Field(default_factory=list)
    tokens_used: int = Field(default=0, ge=0)
    failure_reason: str | None = None
