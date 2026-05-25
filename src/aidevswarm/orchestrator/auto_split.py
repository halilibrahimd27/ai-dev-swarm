"""Cheap circuit-breaker that fires BEFORE the LLM-driven replanner.

If a milestone's most recent SDK session went over either of the
configured caps (``auto_split_max_turns`` /
``auto_split_max_cost_usd``), we emit a mechanical :class:`Split`
that bisects the acceptance criteria into two child milestones.

The intent is to keep the LLM-driven replanner crew from running
on cases that *obviously* need splitting; the crew can still produce
a richer semantic split when called, but auto-split saves a call
when the answer is "yes, halve this".
"""

from __future__ import annotations

from aidevswarm.db.sessions import MilestoneSessionRepo
from aidevswarm.schemas import (
    AcceptanceCriterion,
    Milestone,
    MilestoneSpec,
    Split,
)
from aidevswarm.settings import Settings


class AutoSplitPredictor:
    """Decide whether ``milestone`` should be auto-split before its next run."""

    def __init__(self, settings: Settings, session_repo: MilestoneSessionRepo) -> None:
        self._settings = settings
        self._repo = session_repo

    def predict(self, milestone: Milestone) -> Split | None:
        """Return a :class:`Split` if the milestone's history says it's too big.

        With no recorded session for the milestone, we cannot predict
        anything and return None — the LLM replanner gets to run.
        """
        # Aggregate the worst-case role across Developer + Tester.
        worst_turns = 0
        worst_cost = 0.0
        for role in ("Developer", "Tester"):
            prev = self._repo.latest_for(milestone.id, role)
            if prev is None:
                continue
            worst_turns = max(worst_turns, prev.turns)
            worst_cost = max(worst_cost, prev.cost_usd)

        # No prior attempt — let the LLM replanner think.
        if worst_turns == 0 and worst_cost == 0.0:
            return None

        too_many_turns = worst_turns > self._settings.auto_split_max_turns
        too_expensive = worst_cost > self._settings.auto_split_max_cost_usd
        if not (too_many_turns or too_expensive):
            return None

        return Split(
            action="split",
            milestone_id=milestone.id,
            into=list(_bisect_acceptance_criteria(milestone.spec)),
        )


def _bisect_acceptance_criteria(spec: MilestoneSpec) -> list[MilestoneSpec]:
    """Bisect the acceptance criteria into two child specs.

    With 0 or 1 criteria, we can't bisect meaningfully — we fall back
    to two halves of the description (rough but always produces ≥2
    children, which is the schema invariant).
    """
    criteria = list(spec.acceptance_criteria)
    if len(criteria) >= 2:
        mid = (len(criteria) + 1) // 2
        first, second = criteria[:mid], criteria[mid:]
    else:
        first, second = _split_description_into_criteria(spec.description)

    note_prefix = spec.technical_note + (" " if spec.technical_note else "")
    return [
        MilestoneSpec(
            title=f"{spec.title} (part 1)",
            description=spec.description,
            acceptance_criteria=first,
            technical_note=f"{note_prefix}[AUTO-SPLIT 1/2]",
        ),
        MilestoneSpec(
            title=f"{spec.title} (part 2)",
            description=spec.description,
            acceptance_criteria=second,
            technical_note=f"{note_prefix}[AUTO-SPLIT 2/2]",
        ),
    ]


def _split_description_into_criteria(
    description: str,
) -> tuple[list[AcceptanceCriterion], list[AcceptanceCriterion]]:
    """Fallback when the milestone has fewer than 2 acceptance criteria."""
    first = [AcceptanceCriterion(description=f"First half: {description}", verifier="manual")]
    second = [AcceptanceCriterion(description=f"Second half: {description}", verifier="manual")]
    return first, second
