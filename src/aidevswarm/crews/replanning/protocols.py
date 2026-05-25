"""Replanner crew Protocol.

The tick depends on this interface, not the CrewAI-backed
implementation, so tests substitute a deterministic fake.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from aidevswarm.schemas import Milestone, MilestoneSession, Project, ReplannerAction


class ReplanningCrew(Protocol):
    """Architect + PM crew that emits one :class:`ReplannerAction`."""

    def run(
        self,
        *,
        project: Project,
        next_milestone: Milestone,
        recent_sessions: Sequence[MilestoneSession],
    ) -> ReplannerAction: ...
