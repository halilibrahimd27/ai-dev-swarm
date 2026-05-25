"""Crew-level Protocols.

The orchestrator calls one of these per state transition. Tests
substitute deterministic fakes; runtime substitutes the CrewAI-backed
impls in ``aidevswarm.crews.{ideation,planning,build}.crew``.
"""

from __future__ import annotations

from typing import Protocol

from aidevswarm.schemas import (
    Milestone,
    MilestoneBuildResult,
    MilestoneGraph,
    ProjectSpec,
    ScoredIdea,
)
from aidevswarm.tools import Sandbox, Workspace


class IdeationCrew(Protocol):
    """Trend Scout + Ideator + Critic; returns scored ideas."""

    def run(self) -> list[ScoredIdea]: ...


class PlanningCrew(Protocol):
    """PM + Architect; returns the ordered milestone graph."""

    def run(self, spec: ProjectSpec) -> MilestoneGraph: ...


class BuildCrew(Protocol):
    """Developer(s) + Tester + Reviewer; builds one milestone."""

    def run(
        self,
        *,
        milestone: Milestone,
        workspace: Workspace,
        sandbox: Sandbox,
    ) -> MilestoneBuildResult: ...
