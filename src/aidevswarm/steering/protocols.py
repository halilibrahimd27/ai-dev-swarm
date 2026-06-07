"""Steering repository :class:`Protocol`.

Business code (the prompt renderer + the crew impls) depend on this
interface, not the psycopg-backed implementation.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class SteeringRepo(Protocol):
    """CRUD slice the prompt renderer needs."""

    def add_note(
        self,
        project_id: UUID,
        body: str,
        *,
        author: str = "human",
        target_role: str | None = None,
    ) -> int:
        """Append a steering note; return the row id.

        ``target_role`` of ``None`` makes the note visible to ALL roles
        (delivered once to each); a concrete role restricts it to that role.
        """

    def pull_unconsumed(self, project_id: UUID, role: str) -> list[str]:
        """Atomically read + mark delivered every note for ``project_id``
        not yet delivered to ``role`` and addressed to it (its ``target_role``
        is ``None`` or equals ``role``). Each note is delivered exactly once
        per role. Return the bodies in insertion order; ``[]`` when none
        pending."""
