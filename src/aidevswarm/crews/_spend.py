"""Shared helper: ledger a CrewAI ``kickoff()`` to the spend recorder.

Every crew (ideation, planning, build's Reviewer, replanning) calls
this right after ``crew.kickoff()`` so the per-call token spend lands
in ``token_log``. Attributes are read defensively with ``getattr`` so
this module never imports CrewAI types (the crews import CrewAI lazily
and tests substitute fakes).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from aidevswarm.tools import SpendRecorder


def record_crew_spend(
    recorder: SpendRecorder | None,
    crew_output: Any,
    *,
    project_id: UUID | None,
    milestone_id: UUID | None,
    role: str,
    model: str,
) -> None:
    """Record one crew kickoff's aggregate token usage. No-op if unwired."""
    if recorder is None:
        return
    usage = getattr(crew_output, "token_usage", None)
    if usage is None:
        return
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    if prompt_tokens == 0 and completion_tokens == 0:
        return
    recorder.record(
        project_id=project_id,
        milestone_id=milestone_id,
        role=role,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
