"""Novelty-check schemas.

The Critic role consults a :class:`NoveltyReport` when scoring an
:class:`aidevswarm.schemas.Idea`. Low novelty drives the rejection
path.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Match(BaseModel):
    """One existing project/package that overlaps with a candidate idea."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["github", "pypi"]
    url: str
    title: str
    similarity: float = Field(ge=0.0, le=1.0)


class NoveltyReport(BaseModel):
    """Output of :class:`aidevswarm.crews.ideation.novelty.NoveltyChecker`.

    ``score`` is the inverse of the highest similarity found; 1.0 means
    nothing similar exists, 0.0 means a very close match was found.
    """

    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0.0, le=1.0)
    top_matches: list[Match] = Field(default_factory=list)

    @property
    def is_novel(self) -> bool:
        """Returns True when the idea passes the default 0.6 threshold."""
        return self.score >= 0.6
