"""Exhaustive coverage of legal/illegal state transitions."""

from __future__ import annotations

import pytest

from aidevswarm.orchestrator.state_machine import (
    MILESTONE_TRANSITIONS,
    PROJECT_TRANSITIONS,
    IllegalTransition,
    assert_legal_milestone,
    assert_legal_project,
    legal_milestone_successors,
    legal_project_successors,
)
from aidevswarm.schemas import MilestoneState, ProjectState


@pytest.mark.parametrize(
    ("src", "dst"),
    [
        (ProjectState.QUEUED, ProjectState.PLANNING),
        (ProjectState.PLANNING, ProjectState.AWAITING_APPROVAL),
        (ProjectState.PLANNING, ProjectState.BUILDING),
        (ProjectState.AWAITING_APPROVAL, ProjectState.BUILDING),
        (ProjectState.BUILDING, ProjectState.INTEGRATION),
        (ProjectState.BUILDING, ProjectState.BLOCKED),
        (ProjectState.INTEGRATION, ProjectState.DONE),
        (ProjectState.BLOCKED, ProjectState.BUILDING),
    ],
)
def test_legal_project_happy_paths(src: ProjectState, dst: ProjectState) -> None:
    assert_legal_project(src, dst)
    assert dst in legal_project_successors(src)


def test_kill_is_legal_from_every_non_terminal_project_state() -> None:
    for state in ProjectState:
        if state in {ProjectState.DONE, ProjectState.KILLED}:
            continue
        assert_legal_project(state, ProjectState.KILLED)


@pytest.mark.parametrize(
    ("src", "dst"),
    [
        (ProjectState.QUEUED, ProjectState.DONE),
        (ProjectState.QUEUED, ProjectState.BUILDING),
        (ProjectState.PLANNING, ProjectState.DONE),
        (ProjectState.AWAITING_APPROVAL, ProjectState.INTEGRATION),
        (ProjectState.INTEGRATION, ProjectState.QUEUED),
        (ProjectState.DONE, ProjectState.BUILDING),
        (ProjectState.KILLED, ProjectState.PLANNING),
    ],
)
def test_illegal_project_transitions_raise(src: ProjectState, dst: ProjectState) -> None:
    with pytest.raises(IllegalTransition) as info:
        assert_legal_project(src, dst)
    assert info.value.kind == "project"
    assert info.value.src == src.value
    assert info.value.dst == dst.value


def test_terminal_project_states_have_no_successors() -> None:
    assert PROJECT_TRANSITIONS[ProjectState.DONE] == frozenset()
    assert PROJECT_TRANSITIONS[ProjectState.KILLED] == frozenset()


@pytest.mark.parametrize(
    ("src", "dst"),
    [
        (MilestoneState.PENDING, MilestoneState.BUILDING),
        (MilestoneState.BUILDING, MilestoneState.DONE),
        (MilestoneState.BUILDING, MilestoneState.FAILED),
        (MilestoneState.FAILED, MilestoneState.BUILDING),
    ],
)
def test_legal_milestone_paths(src: MilestoneState, dst: MilestoneState) -> None:
    assert_legal_milestone(src, dst)
    assert dst in legal_milestone_successors(src)


@pytest.mark.parametrize(
    ("src", "dst"),
    [
        (MilestoneState.PENDING, MilestoneState.DONE),
        (MilestoneState.DONE, MilestoneState.BUILDING),
        (MilestoneState.FAILED, MilestoneState.DONE),
    ],
)
def test_illegal_milestone_transitions_raise(src: MilestoneState, dst: MilestoneState) -> None:
    with pytest.raises(IllegalTransition):
        assert_legal_milestone(src, dst)


def test_transition_tables_cover_every_enum_value() -> None:
    """Catch regressions where someone adds a state but forgets the table."""
    assert set(PROJECT_TRANSITIONS) == set(ProjectState)
    assert set(MILESTONE_TRANSITIONS) == set(MilestoneState)
