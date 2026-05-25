"""Steering repository :class:`Protocol`.

Business code (the prompt renderer + the crew impls) depend on this
interface, not the psycopg-backed implementation.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class SteeringRepo(Protocol):
    """CRUD slice the prompt renderer needs."""

    def add_note(self, project_id: UUID, body: str, *, author: str = "human") -> int:
        """Append a steering note; return the row id."""

    def pull_unconsumed(self, project_id: UUID, role: str) -> list[str]:
        """Atomically read + mark consumed every still-unconsumed note for
        ``project_id``, attributing the consumption to ``role``. Return
        the note bodies in insertion order. Returns ``[]`` when nothing
        is pending."""
