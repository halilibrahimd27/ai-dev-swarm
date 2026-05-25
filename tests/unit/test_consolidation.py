"""Unit tests for the consolidation cadence helper."""

from __future__ import annotations

from uuid import uuid4

import pytest

from aidevswarm.orchestrator.consolidation import (
    CONSOLIDATION_MARKER,
    build_consolidation_spec,
    should_insert_consolidation,
)
from aidevswarm.schemas import (
    AcceptanceCriterion,
    Milestone,
    MilestoneSpec,
    MilestoneState,
)


def _m(
    state: MilestoneState = MilestoneState.DONE,
    *,
    title: str = "m",
    note: str = "",
    ordinal: int = 0,
) -> Milestone:
    return Milestone(
        project_id=uuid4(),
        ordinal=ordinal,
        title=title,
        spec=MilestoneSpec(
            title=title,
            description="d",
            acceptance_criteria=[
                AcceptanceCriterion(description="x", verifier="pytest")
            ],
            technical_note=note,
        ),
        state=state,
    )


def test_no_done_milestones_no_consolidation() -> None:
    assert should_insert_consolidation([], every=5) is False


def test_below_threshold_no_consolidation() -> None:
    milestones = [_m() for _ in range(4)]
    assert should_insert_consolidation(milestones, every=5) is False


def test_exactly_threshold_triggers_consolidation() -> None:
    milestones = [_m() for _ in range(5)]
    assert should_insert_consolidation(milestones, every=5) is True


def test_pending_milestones_dont_count() -> None:
    milestones = [_m() for _ in range(4)] + [_m(state=MilestoneState.PENDING)]
    assert should_insert_consolidation(milestones, every=5) is False


def test_consolidation_milestone_resets_counter() -> None:
    """5 done -> consolidation done -> 4 more done = 4 since last, no trigger."""
    milestones = (
        [_m() for _ in range(5)]
        + [_m(note=CONSOLIDATION_MARKER + " tidy")]
        + [_m() for _ in range(4)]
    )
    assert should_insert_consolidation(milestones, every=5) is False


def test_consolidation_then_five_more_triggers_again() -> None:
    milestones = (
        [_m() for _ in range(5)]
        + [_m(note=CONSOLIDATION_MARKER + " tidy")]
        + [_m() for _ in range(5)]
    )
    assert should_insert_consolidation(milestones, every=5) is True


def test_zero_or_negative_every_returns_false() -> None:
    milestones = [_m() for _ in range(10)]
    assert should_insert_consolidation(milestones, every=0) is False
    assert should_insert_consolidation(milestones, every=-1) is False


def test_build_consolidation_spec_marker_present() -> None:
    spec = build_consolidation_spec()
    assert CONSOLIDATION_MARKER in spec.technical_note
    # The spec text must explicitly forbid new features.
    assert "do not add new" in spec.description.lower()
    assert any("make verify" in c.description for c in spec.acceptance_criteria)


@pytest.mark.parametrize("every", [3, 5, 7])
def test_consolidation_only_fires_at_multiples(every: int) -> None:
    for count in range(every * 2 + 1):
        milestones = [_m() for _ in range(count)]
        expected = count > 0 and count % every == 0
        assert should_insert_consolidation(milestones, every=every) is expected
