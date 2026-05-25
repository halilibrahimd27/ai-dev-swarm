"""Project and milestone state enums plus the core Pydantic row models.

The enums are the authoritative state vocabulary; the
:mod:`aidevswarm.orchestrator.state_machine` module owns transition
legality.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from aidevswarm._time import utc_now as _utc_now


class ProjectState(StrEnum):
    """High-level lifecycle state of a project, per ARCHITECTURE.md §2."""

    QUEUED = "queued"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    BUILDING = "building"
    INTEGRATION = "integration"
    DONE = "done"
    BLOCKED = "blocked"
    KILLED = "killed"


class MilestoneState(StrEnum):
    """State of a single milestone inside a project."""

    PENDING = "pending"
    BUILDING = "building"
    DONE = "done"
    FAILED = "failed"


TERMINAL_PROJECT_STATES: frozenset[ProjectState] = frozenset(
    {ProjectState.DONE, ProjectState.KILLED}
)
TERMINAL_MILESTONE_STATES: frozenset[MilestoneState] = frozenset({MilestoneState.DONE})


class ProjectSpec(BaseModel):
    """The Critic-approved, Ideator-authored project-level spec."""

    model_config = ConfigDict(extra="forbid")

    title: str
    summary: str
    rationale: str
    stack: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    score: int = Field(ge=0, le=100)


class Project(BaseModel):
    """A row in the ``projects`` table."""

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    name: str
    spec: ProjectSpec
    state: ProjectState = ProjectState.QUEUED
    github_repo: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    def is_terminal(self) -> bool:
        """True when the project will not transition further."""
        return self.state in TERMINAL_PROJECT_STATES
