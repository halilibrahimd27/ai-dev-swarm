"""Consolidation milestones — Phase 4 cadence.

After every Nth (default 5) successful milestone the scheduler
injects a "tidy + verify, no new features" milestone. The Reviewer
template detects the ``[CONSOLIDATION]`` marker in
``technical_note`` and rejects any feature addition.
"""

from __future__ import annotations

from collections.abc import Sequence

from aidevswarm.schemas import (
    AcceptanceCriterion,
    Milestone,
    MilestoneSpec,
    MilestoneState,
)

CONSOLIDATION_MARKER = "[CONSOLIDATION]"


def should_insert_consolidation(
    milestones: Sequence[Milestone], *, every: int = 5
) -> bool:
    """True when we just crossed an Nth-completed-milestone boundary.

    Counts ``done`` milestones (excluding existing consolidation
    milestones themselves) since the last consolidation. When that
    count is a positive multiple of ``every``, a consolidation is due.
    """
    if every <= 0:
        return False
    done_since_last_consolidation = 0
    for m in milestones:
        if _is_consolidation(m):
            done_since_last_consolidation = 0
            continue
        if m.state is MilestoneState.DONE:
            done_since_last_consolidation += 1
    return (
        done_since_last_consolidation > 0
        and done_since_last_consolidation % every == 0
    )


def build_consolidation_spec() -> MilestoneSpec:
    """The no-new-features ``MilestoneSpec`` the scheduler injects.

    The marker in ``technical_note`` is how the Reviewer template
    knows to enforce the "no new public functions" rule.
    """
    return MilestoneSpec(
        title="Consolidation pass",
        description=(
            "Read every artefact added since the last consolidation. "
            "Refactor for cohesion + readability; tighten types; update "
            "the README and any ADRs; run `make verify`. "
            "DO NOT add new features or new public APIs."
        ),
        acceptance_criteria=[
            AcceptanceCriterion(description="`make verify` exits 0", verifier="lint"),
            AcceptanceCriterion(
                description="No new public functions, classes, or CLI args",
                verifier="manual",
            ),
            AcceptanceCriterion(
                description="README + relevant ADRs reflect current behaviour",
                verifier="manual",
            ),
        ],
        technical_note=(
            f"{CONSOLIDATION_MARKER} Tidy + verify; the Reviewer rejects feature-adds."
        ),
    )


def _is_consolidation(milestone: Milestone) -> bool:
    return CONSOLIDATION_MARKER in (milestone.spec.technical_note or "")
