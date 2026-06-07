"""Idea / scored-idea models produced by the Ideation crew."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from aidevswarm._time import utc_now as _utc_now


class Idea(BaseModel):
    """One raw idea produced by the Ideator agent."""

    model_config = ConfigDict(extra="forbid")

    title: str
    summary: str
    rationale: str
    stack: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class CriticScores(BaseModel):
    """Per-criterion scores from the Critic, 0-100 each.

    Weights live with the Critic prompt; the aggregated ``total`` is
    re-computed from these sub-scores (:attr:`weighted_total`) to keep the
    scoring policy honest — the LLM's own ``total`` field is NOT trusted.
    """

    model_config = ConfigDict(extra="forbid")

    depth_ambition: int = Field(ge=0, le=100)
    usefulness_niche: int = Field(ge=0, le=100)
    novelty: int = Field(ge=0, le=100)
    decomposability: int = Field(ge=0, le=100)
    buildability: int = Field(ge=0, le=100)

    @property
    def weighted_total(self) -> int:
        """The canonical 0-100 score: the rubric-weighted sum (ARCHITECTURE
        §6). This — not the LLM-emitted ``total`` — is what the gate uses, so
        the model can't inflate its own score past the threshold."""
        return round(
            self.depth_ambition * 0.30
            + self.usefulness_niche * 0.25
            + self.novelty * 0.20
            + self.decomposability * 0.15
            + self.buildability * 0.10
        )


class ScoredIdea(BaseModel):
    """An Ideator idea plus the Critic's per-criterion scores."""

    model_config = ConfigDict(extra="forbid")

    idea: Idea
    scores: CriticScores
    total: int = Field(ge=0, le=100)
    rejected_reason: str | None = None


class IdeaEvaluation(BaseModel):
    """A persisted record of one scored idea + the accept/reject verdict.

    Stored for EVERY idea the Critic scores (per ideation round) so the
    control plane can show *why* a project was started or an idea
    dropped. ``accepted`` is true for the one idea that cleared the gate
    and became a project (``project_id`` then points at it).
    """

    model_config = ConfigDict(extra="forbid")

    id: int = 0
    round: int = 0
    title: str
    summary: str = ""
    scores: CriticScores
    total: int = Field(ge=0, le=100)
    novel: bool = True
    accepted: bool = False
    rejected_reason: str | None = None
    project_id: UUID | None = None
    created_at: datetime = Field(default_factory=_utc_now)

    @classmethod
    def from_scored(
        cls,
        scored: ScoredIdea,
        *,
        round: int,
        accepted: bool,
        project_id: UUID | None = None,
    ) -> IdeaEvaluation:
        """Build an evaluation row from a :class:`ScoredIdea` + verdict."""
        return cls(
            round=round,
            title=scored.idea.title,
            summary=scored.idea.summary,
            scores=scored.scores,
            total=scored.total,
            # `novel` reflects the NOVELTY signal specifically, not "rejected
            # for any reason at all". The novelty/dedup gate zeroes the
            # novelty sub-score when it fires, so novelty == 0 ⟺ not novel;
            # a low-score rejection for OTHER reasons no longer mislabels a
            # genuinely-original idea as un-novel.
            novel=scored.scores.novelty > 0,
            accepted=accepted,
            rejected_reason=scored.rejected_reason,
            project_id=project_id,
        )
