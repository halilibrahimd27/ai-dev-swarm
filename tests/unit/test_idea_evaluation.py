"""IdeaEvaluation.from_scored maps a ScoredIdea + verdict to a row."""

from __future__ import annotations

from aidevswarm.schemas import CriticScores, Idea, IdeaEvaluation, ScoredIdea


def _scored(total: int, reason: str | None) -> ScoredIdea:
    return ScoredIdea(
        idea=Idea(title="T", summary="s", rationale="r", stack=["python"], tags=["x"]),
        scores=CriticScores(
            depth_ambition=total,
            usefulness_niche=total,
            novelty=total,
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


def test_from_scored_rejected_carries_reason_and_not_novel() -> None:
    ev = IdeaEvaluation.from_scored(
        _scored(40, "low novelty; matches foo"), round=1, accepted=False
    )
    assert ev.accepted is False
    assert ev.novel is False  # a rejected_reason implies not-novel
    assert ev.rejected_reason == "low novelty; matches foo"
