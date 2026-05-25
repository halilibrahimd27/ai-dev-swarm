"""Tiny time helpers shared across modules.

``datetime.utcnow`` is deprecated in Python 3.12 — using a single
helper means we only have to keep the timezone story right in one
place.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(UTC)
