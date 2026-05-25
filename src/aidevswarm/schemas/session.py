"""Pydantic model for a ``milestone_sessions`` row."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from aidevswarm._time import utc_now as _utc_now


class MilestoneSession(BaseModel):
    """One Claude Agent SDK invocation, persisted for resume.

    The build crew reads the most recent row for a given
    ``(milestone_id, role)`` and passes ``resume=session_id`` to the
    SDK on retry so the SDK conversation continues rather than
    restarting.
    """

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    milestone_id: UUID
    role: str
    session_id: str
    cost_usd: float = Field(ge=0)
    turns: int = Field(ge=0)
    finished_at: datetime = Field(default_factory=_utc_now)
