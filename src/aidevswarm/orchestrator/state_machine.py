"""Pure-Python project / milestone state-transition guard.

This module has **zero** I/O. It exists so the legal-transition table is
unit-testable in isolation and so callers cannot accidentally jump to an
arbitrary state from anywhere.
"""

from __future__ import annotations

from typing import Final

from aidevswarm.schemas import MilestoneState, ProjectState


class IllegalTransition(RuntimeError):
    """Raised when a caller attempts a non-whitelisted state transition."""

    def __init__(self, kind: str, src: str, dst: str) -> None:
        super().__init__(f"illegal {kind} transition: {src} -> {dst}")
        self.kind = kind
        self.src = src
        self.dst = dst


# Project transitions are defined explicitly per ARCHITECTURE.md §2.
PROJECT_TRANSITIONS: Final[dict[ProjectState, frozenset[ProjectState]]] = {
    ProjectState.QUEUED: frozenset({ProjectState.PLANNING, ProjectState.KILLED}),
    ProjectState.PLANNING: frozenset(
        {
            ProjectState.AWAITING_APPROVAL,
            ProjectState.BUILDING,  # when require_approval is false
            ProjectState.KILLED,
        }
    ),
    ProjectState.AWAITING_APPROVAL: frozenset({ProjectState.BUILDING, ProjectState.KILLED}),
    ProjectState.BUILDING: frozenset(
        {ProjectState.INTEGRATION, ProjectState.BLOCKED, ProjectState.KILLED}
    ),
    ProjectState.INTEGRATION: frozenset(
        {ProjectState.DONE, ProjectState.BLOCKED, ProjectState.KILLED}
    ),
    ProjectState.BLOCKED: frozenset({ProjectState.BUILDING, ProjectState.KILLED}),
    ProjectState.DONE: frozenset(),
    ProjectState.KILLED: frozenset(),
}


MILESTONE_TRANSITIONS: Final[dict[MilestoneState, frozenset[MilestoneState]]] = {
    MilestoneState.PENDING: frozenset({MilestoneState.BUILDING}),
    MilestoneState.BUILDING: frozenset({MilestoneState.DONE, MilestoneState.FAILED}),
    MilestoneState.FAILED: frozenset({MilestoneState.BUILDING}),
    MilestoneState.DONE: frozenset(),
}


def assert_legal_project(src: ProjectState, dst: ProjectState) -> None:
    """Raise :class:`IllegalTransition` if ``src -> dst`` is not allowed."""
    if dst not in PROJECT_TRANSITIONS.get(src, frozenset()):
        raise IllegalTransition("project", src.value, dst.value)


def assert_legal_milestone(src: MilestoneState, dst: MilestoneState) -> None:
    """Raise :class:`IllegalTransition` if the milestone hop is not allowed."""
    if dst not in MILESTONE_TRANSITIONS.get(src, frozenset()):
        raise IllegalTransition("milestone", src.value, dst.value)


def legal_project_successors(src: ProjectState) -> frozenset[ProjectState]:
    """Return all states a project may legally transition to from ``src``."""
    return PROJECT_TRANSITIONS.get(src, frozenset())


def legal_milestone_successors(src: MilestoneState) -> frozenset[MilestoneState]:
    """Return all states a milestone may legally transition to from ``src``."""
    return MILESTONE_TRANSITIONS.get(src, frozenset())
