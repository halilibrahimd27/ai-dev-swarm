"""Hypothesis property tests for the state machine guard.

These are invariants the transition tables MUST satisfy regardless of
input — total functions over the cross-product of states, closure
under terminal flags, and the killable-from-anywhere rule.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aidevswarm.orchestrator.state_machine import (
    MILESTONE_TRANSITIONS,
    PROJECT_TRANSITIONS,
    IllegalTransition,
    assert_legal_milestone,
    assert_legal_project,
    legal_milestone_successors,
    legal_project_successors,
)
from aidevswarm.schemas import (
    TERMINAL_MILESTONE_STATES,
    TERMINAL_PROJECT_STATES,
    MilestoneState,
    ProjectState,
)

pytestmark = pytest.mark.property

project_states = st.sampled_from(list(ProjectState))
milestone_states = st.sampled_from(list(MilestoneState))


@given(src=project_states, dst=project_states)
def test_project_transition_is_total(src: ProjectState, dst: ProjectState) -> None:
    """``assert_legal_project`` raises or returns for every pair — never UB."""
    if dst in legal_project_successors(src):
        assert_legal_project(src, dst)
    else:
        with pytest.raises(IllegalTransition):
            assert_legal_project(src, dst)


@given(src=milestone_states, dst=milestone_states)
def test_milestone_transition_is_total(src: MilestoneState, dst: MilestoneState) -> None:
    if dst in legal_milestone_successors(src):
        assert_legal_milestone(src, dst)
    else:
        with pytest.raises(IllegalTransition):
            assert_legal_milestone(src, dst)


@given(src=project_states)
def test_terminal_project_states_have_zero_successors(src: ProjectState) -> None:
    successors = legal_project_successors(src)
    if src in TERMINAL_PROJECT_STATES:
        assert successors == frozenset()


@given(src=milestone_states)
def test_terminal_milestone_states_have_zero_successors(src: MilestoneState) -> None:
    successors = legal_milestone_successors(src)
    if src in TERMINAL_MILESTONE_STATES:
        assert successors == frozenset()


@given(src=project_states)
def test_kill_is_always_reachable_from_non_terminal_project_state(
    src: ProjectState,
) -> None:
    if src in TERMINAL_PROJECT_STATES:
        return
    assert ProjectState.KILLED in legal_project_successors(src)


def test_every_enum_value_has_a_transition_table_entry() -> None:
    assert set(PROJECT_TRANSITIONS) == set(ProjectState)
    assert set(MILESTONE_TRANSITIONS) == set(MilestoneState)


@given(src=project_states)
def test_successor_set_is_a_subset_of_states(src: ProjectState) -> None:
    """Whatever the successor set is, every element must itself be a valid state."""
    assert legal_project_successors(src) <= set(ProjectState)


@given(src=milestone_states)
def test_milestone_successor_set_is_a_subset_of_states(src: MilestoneState) -> None:
    assert legal_milestone_successors(src) <= set(MilestoneState)
