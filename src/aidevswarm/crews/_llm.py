"""Build a CrewAI ``LLM`` with an explicit output-token cap.

CrewAI's default ``max_tokens`` is low enough that a full milestone-graph
(or scored-idea) JSON truncates mid-string and fails to parse — which
blocks the project right after paying for the call. Constructing the LLM
with a generous cap fixes that. The import is local so test stacks that
substitute fakes never import CrewAI.
"""

from __future__ import annotations

from typing import Any


def make_llm(model: str, max_tokens: int) -> Any:
    """Return a ``crewai.LLM`` for ``model`` capped at ``max_tokens`` output."""
    from crewai import LLM

    return LLM(model=model, max_tokens=max_tokens)
