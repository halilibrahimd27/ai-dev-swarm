"""Cheap circuit-breaker that fires BEFORE the LLM-driven replanner.

If a milestone's most recent SDK session went over either of the
configured caps (``auto_split_max_turns`` /
``auto_split_max_cost_usd``), we emit a mechanical :class:`Split`
that bisects the acceptance criteria into two child milestones.

The intent is to keep the LLM-driven replanner crew from running
on cases that *obviously* need splitting; the crew can still produce
a richer semantic split when called, but auto-split saves a call
when the answer is "yes, halve this".

**Why this only fires ONCE per milestone lineage.** Mechanical
bisection halves the acceptance criteria but keeps the description, so a
child is not guaranteed to be smaller *work*. If we re-split children
unconditionally, an over-budget milestone whose real scope lives in its
description (not its criteria) recurses forever — each pass appends
``(part 1)`` to the title and doubles the milestone count while the
Developer keeps hitting ``max_turns`` on the same unchanged scope. (This
actually happened: a milestone became ``... (part 1) (part 1) (part 1)``
and burned budget with zero progress.) So auto-split refuses to act when
it can't meaningfully shrink the work: an already-auto-split child, or a
milestone with fewer than two criteria, is handed to the LLM replanner
(which can split semantically) or, failing that, to the retry/block path.
"""

from __future__ import annotations

from aidevswarm.db.sessions import MilestoneSessionRepo
from aidevswarm.schemas import (
    Milestone,
    MilestoneSpec,
    Split,
)
from aidevswarm.settings import Settings

# Stamped on a child's technical_note when auto-split bisects a milestone.
# Its presence means "this milestone is already the product of an
# auto-split" — re-splitting it mechanically would just recurse.
_AUTO_SPLIT_MARKER = "[AUTO-SPLIT"


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

        # Loop guard: never mechanically re-split a milestone we can't
        # meaningfully shrink (see module docstring). Defer to the LLM
        # replanner / retry / block instead of recursing forever.
        if _AUTO_SPLIT_MARKER in milestone.spec.technical_note:
            return None
        if len(milestone.spec.acceptance_criteria) < 2:
            return None

        return Split(
            action="split",
            milestone_id=milestone.id,
            into=list(_bisect_acceptance_criteria(milestone.spec)),
        )


def _bisect_acceptance_criteria(spec: MilestoneSpec) -> list[MilestoneSpec]:
    """Bisect a ≥2-criteria spec into two child specs.

    The caller guarantees at least two acceptance criteria, so the
    children genuinely differ (each owns a disjoint half) and the schema
    invariant (``into`` has ≥2 entries) holds.
    """
    criteria = list(spec.acceptance_criteria)
    mid = (len(criteria) + 1) // 2
    first, second = criteria[:mid], criteria[mid:]

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
