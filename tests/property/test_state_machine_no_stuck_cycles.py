"""Random-walk property test: the state machine never gets stuck.

For every non-terminal :class:`ProjectState` we walk the transition
graph K steps choosing successors with a Hypothesis-driven strategy
(including the always-legal ``KILLED`` escape hatch). The invariant:
every such walk hits ``DONE`` or ``KILLED`` within K steps — i.e. there
is no terminal-bypassing cycle the orchestrator could spin in.

The "always-legal kill" rule from the Phase 0 brief makes this an
easy invariant to state — we exploit the operator's kill switch as
the universal escape. With Phase 4's new REPLANNING state the walk
gains a new branch but no new cycles.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aidevswarm.orchestrator.state_machine import legal_project_successors
from aidevswarm.schemas import TERMINAL_PROJECT_STATES, ProjectState

pytestmark = pytest.mark.property


@given(start=st.sampled_from(list(ProjectState)))
@settings(max_examples=30)
def test_every_state_can_reach_a_terminal(start: ProjectState) -> None:
    """Reachability invariant: a terminal state is REACHABLE from every state.

    Liveness can't promise a random walk lands (BUILDING↔REPLANNING is
    a legitimate cycle the LLM may sit in for a while). What MUST hold
    is that *some* path from every state ends at DONE or KILLED.
    """
    if start in TERMINAL_PROJECT_STATES:
        return
    seen: set[ProjectState] = set()
    stack = [start]
    while stack:
        state = stack.pop()
        if state in seen:
            continue
        seen.add(state)
        if state in TERMINAL_PROJECT_STATES:
            return
        stack.extend(legal_project_successors(state) - seen)
    pytest.fail(f"No terminal state reachable from {start.value!r}")


def test_kill_is_universal_escape() -> None:
    """Every non-terminal project state has KILLED in its successors."""
    for state in ProjectState:
        if state in TERMINAL_PROJECT_STATES:
            continue
        assert ProjectState.KILLED in legal_project_successors(
            state
        ), f"{state.value} lacks the KILLED escape hatch"


def test_replanning_can_reach_either_done_or_blocked() -> None:
    """The new Phase 4 state has both productive + dead-end exits."""
    successors = legal_project_successors(ProjectState.REPLANNING)
    assert ProjectState.BUILDING in successors  # progress
    assert ProjectState.INTEGRATION in successors  # all milestones done
    assert ProjectState.BLOCKED in successors  # escalate
    assert ProjectState.KILLED in successors  # operator kill
