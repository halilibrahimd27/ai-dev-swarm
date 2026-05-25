"""Crew-level Protocols.

The orchestrator calls one of these per state transition. Tests
substitute deterministic fakes; runtime substitutes the CrewAI-backed
impls in ``aidevswarm.crews.{ideation,planning,build}.crew``.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from aidevswarm.schemas import (
    Milestone,
    MilestoneBuildResult,
    MilestoneGraph,
    ProjectSpec,
    ScoredIdea,
)
from aidevswarm.tools import Sandbox, Workspace


class IdeationCrew(Protocol):
    """Trend Scout + Ideator + Critic; returns scored ideas.

    Ideation runs BEFORE any project exists, so no project-scoped
    steering notes are pulled at this layer.
    """

    def run(self) -> list[ScoredIdea]: ...


class PlanningCrew(Protocol):
    """PM + Architect; returns the ordered milestone graph.

    Takes ``project_id`` so the impl can pull per-role steering notes
    from :class:`aidevswarm.steering.SteeringRepo` for the project just
    before kickoff.
    """

    def run(self, project_id: UUID, spec: ProjectSpec) -> MilestoneGraph: ...


class BuildCrew(Protocol):
    """Developer(s) + Tester + Reviewer; builds one milestone.

    The ``Milestone`` already carries ``project_id``; the impl pulls
    per-role steering notes for that project before kickoff.
    """

    def run(
        self,
        *,
        milestone: Milestone,
        workspace: Workspace,
        sandbox: Sandbox,
    ) -> MilestoneBuildResult: ...
