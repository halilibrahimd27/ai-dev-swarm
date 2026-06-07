"""Pure-Python project / milestone state-transition guard.

This module has **zero** I/O. It lives in the ``schemas`` layer (the lowest
domain layer) so BOTH the ``db`` repositories and the ``orchestrator`` can
enforce the transition tables without an upward (db -> orchestrator) import.
The legal-transition table is unit-testable in isolation and callers cannot
accidentally jump to an arbitrary state from anywhere.
"""

from __future__ import annotations

from typing import Final

from aidevswarm.schemas.project import MilestoneState, ProjectState


class IllegalTransition(RuntimeError):
    """Raised when a caller attempts a non-whitelisted state transition."""

    def __init__(self, kind: str, src: str, dst: str) -> None:
        super().__init__(f"illegal {kind} transition: {src} -> {dst}")
        self.kind = kind
        self.src = src
        self.dst = dst


# Project transitions are defined explicitly per ARCHITECTURE.md §2.
# A non-transient crash in ANY non-terminal state must be able to land in
# BLOCKED ("stopped, needs a human, resumable") — a crash while PLANNING or
# QUEUED genuinely needs a look too, not just a crash mid-build. So every
# pre-build state also lists BLOCKED. BLOCKED can resume to BUILDING (work
# exists) OR back to PLANNING (blocked before any milestone was produced).
PROJECT_TRANSITIONS: Final[dict[ProjectState, frozenset[ProjectState]]] = {
    ProjectState.QUEUED: frozenset(
        {ProjectState.PLANNING, ProjectState.BLOCKED, ProjectState.KILLED}
    ),
    ProjectState.PLANNING: frozenset(
        {
            ProjectState.AWAITING_APPROVAL,
            ProjectState.BUILDING,  # when require_approval is false
            ProjectState.BLOCKED,  # a non-transient planning crash
            ProjectState.KILLED,
        }
    ),
    ProjectState.AWAITING_APPROVAL: frozenset(
        {ProjectState.BUILDING, ProjectState.BLOCKED, ProjectState.KILLED}
    ),
    ProjectState.BUILDING: frozenset(
        {
            ProjectState.REPLANNING,
            ProjectState.INTEGRATION,
            ProjectState.BLOCKED,
            ProjectState.KILLED,
        }
    ),
    ProjectState.REPLANNING: frozenset(
        {
            ProjectState.BUILDING,
            ProjectState.INTEGRATION,
            ProjectState.BLOCKED,
            ProjectState.KILLED,
        }
    ),
    ProjectState.INTEGRATION: frozenset(
        {ProjectState.DONE, ProjectState.BLOCKED, ProjectState.KILLED}
    ),
    ProjectState.BLOCKED: frozenset(
        {ProjectState.BUILDING, ProjectState.PLANNING, ProjectState.KILLED}
    ),
    ProjectState.DONE: frozenset(),
    ProjectState.KILLED: frozenset(),
}


# Includes the RESET + circuit-breaker edges the orchestrator really uses, so
# the guard can be enforced at the repo boundary without rejecting a legitimate
# write:
#   * BUILDING -> PENDING : orphan reclaim (restart / mid-build budget pause)
#                           resets an in-flight milestone to be re-attempted.
#   * PENDING/FAILED -> FAILED : the per-milestone token circuit-breaker records
#                           a failed attempt BEFORE the build starts.
# DONE stays terminal — flipping a done milestone back is the bug the guard
# exists to catch.
MILESTONE_TRANSITIONS: Final[dict[MilestoneState, frozenset[MilestoneState]]] = {
    MilestoneState.PENDING: frozenset({MilestoneState.BUILDING, MilestoneState.FAILED}),
    MilestoneState.BUILDING: frozenset(
        {MilestoneState.DONE, MilestoneState.FAILED, MilestoneState.PENDING}
    ),
    MilestoneState.FAILED: frozenset({MilestoneState.BUILDING, MilestoneState.FAILED}),
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
