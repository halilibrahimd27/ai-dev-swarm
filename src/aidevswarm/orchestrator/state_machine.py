"""Re-export shim — the state-transition guard moved to the schemas layer.

The pure transition tables + guards now live in
:mod:`aidevswarm.schemas.state_machine` so BOTH the ``db`` repositories and
the ``orchestrator`` can enforce them without an upward (db -> orchestrator)
import. This module preserves the historical
``aidevswarm.orchestrator.state_machine`` import path.
"""

from __future__ import annotations

from aidevswarm.schemas.state_machine import (
    MILESTONE_TRANSITIONS,
    PROJECT_TRANSITIONS,
    IllegalTransition,
    assert_legal_milestone,
    assert_legal_project,
    legal_milestone_successors,
    legal_project_successors,
)

__all__ = [
    "MILESTONE_TRANSITIONS",
    "PROJECT_TRANSITIONS",
    "IllegalTransition",
    "assert_legal_milestone",
    "assert_legal_project",
    "legal_milestone_successors",
    "legal_project_successors",
]
