"""Tolerant-parse helpers for the loose CrewAI/LLM JSON boundary.

The internal Pydantic models use ``extra='forbid'`` so business code can't
typo a field. But LLMs routinely add helpful-looking extras (e.g. an
``"id": "m1"`` on each milestone). Without filtering, every entry fails
validation and the whole plan is dropped — the project then blocks right
after we paid for the call. ``keep_known`` drops unknown keys at the
LLM boundary while leaving the models strict everywhere else.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def keep_known(model_cls: type[BaseModel], data: dict[str, Any]) -> dict[str, Any]:
    """Return ``data`` with only the keys ``model_cls`` actually declares."""
    fields = set(model_cls.model_fields)
    return {k: v for k, v in data.items() if k in fields}


def clean_milestone_dict(
    entry: dict[str, Any],
    milestone_cls: type[BaseModel],
    criterion_cls: type[BaseModel],
) -> dict[str, Any]:
    """Strip LLM extras from a milestone dict and its nested criteria."""
    cleaned = keep_known(milestone_cls, entry)
    crits = cleaned.get("acceptance_criteria")
    if isinstance(crits, list):
        cleaned["acceptance_criteria"] = [
            keep_known(criterion_cls, c) if isinstance(c, dict) else c for c in crits
        ]
    return cleaned


def loads_lenient(raw: Any) -> Any:
    """Parse possibly-broken LLM JSON. Never raises.

    Real Opus output for a big milestone graph routinely arrives wrapped
    in ``` fences, with a missing comma, an unescaped char, or truncated
    mid-string — plain ``json.loads`` then fails and the whole plan is
    lost (after we paid for it). ``json_repair`` fixes the common LLM
    mistakes (and closes truncated structures) before loading. Non-str
    input is returned as-is.
    """
    if not isinstance(raw, str):
        return raw
    import json_repair

    return json_repair.loads(raw)
