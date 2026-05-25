"""Idea / scored-idea models produced by the Ideation crew."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


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
    re-computed by the orchestrator to keep the scoring policy honest.
    """

    model_config = ConfigDict(extra="forbid")

    depth_ambition: int = Field(ge=0, le=100)
    usefulness_niche: int = Field(ge=0, le=100)
    novelty: int = Field(ge=0, le=100)
    decomposability: int = Field(ge=0, le=100)
    buildability: int = Field(ge=0, le=100)


class ScoredIdea(BaseModel):
    """An Ideator idea plus the Critic's per-criterion scores."""

    model_config = ConfigDict(extra="forbid")

    idea: Idea
    scores: CriticScores
    total: int = Field(ge=0, le=100)
    rejected_reason: str | None = None
