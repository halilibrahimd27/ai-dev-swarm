"""IdeaEvaluation.from_scored maps a ScoredIdea + verdict to a row."""

from __future__ import annotations

from aidevswarm.schemas import CriticScores, Idea, IdeaEvaluation, ScoredIdea


def _scored(total: int, reason: str | None, *, novelty: int | None = None) -> ScoredIdea:
    nov = total if novelty is None else novelty
    return ScoredIdea(
        idea=Idea(title="T", summary="s", rationale="r", stack=["python"], tags=["x"]),
        scores=CriticScores(
            depth_ambition=total,
            usefulness_niche=total,
            novelty=nov,
            decomposability=total,
            buildability=total,
        ),
        total=total,
        rejected_reason=reason,
    )


def test_from_scored_accepted_is_novel_with_no_reason() -> None:
    ev = IdeaEvaluation.from_scored(_scored(85, None), round=2, accepted=True, project_id=None)
    assert ev.accepted is True
    assert ev.novel is True
    assert ev.round == 2
    assert ev.title == "T"
    assert ev.total == 85
    assert ev.rejected_reason is None


def test_from_scored_novelty_rejection_is_not_novel() -> None:
    # The novelty/dedup gate zeroes the novelty sub-score when it fires, so a
    # genuine novelty rejection has novelty == 0 → novel is False.
    ev = IdeaEvaluation.from_scored(
        _scored(40, "low novelty; matches foo", novelty=0), round=1, accepted=False
    )
    assert ev.accepted is False
    assert ev.novel is False
    assert ev.rejected_reason == "low novelty; matches foo"


def test_from_scored_non_novelty_rejection_stays_novel() -> None:
    # Rejected for a LOW non-novelty score, but the idea IS original: `novel`
    # must stay True. The old code mislabelled ANY rejection as not-novel.
    ev = IdeaEvaluation.from_scored(
        _scored(40, "below score threshold", novelty=75), round=1, accepted=False
    )
    assert ev.accepted is False
    assert ev.novel is True
    assert ev.rejected_reason == "below score threshold"
