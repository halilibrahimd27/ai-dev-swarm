"""projects.is_paused — durable pause flag.

Pause used to live in Redis (``aidevswarm:pause:<project_id>``), which
doesn't survive a container reset. A rebuild wiped the pause key and the
project would have resumed had the daily-budget guard not also been
exhausted. Pause is now stored on the project row itself, so a restart
preserves it.

Backfill: any project whose ``status_detail`` already reads
``"paused by operator"`` is set to ``is_paused = true``, so the transition
is automatic — no operator re-pause needed.

Revision ID: 20260529_0007
Revises: 20260529_0006
Create Date: 2026-05-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260529_0007"
down_revision: str | Sequence[str] | None = "20260529_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "is_paused",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.execute("UPDATE projects SET is_paused = true WHERE status_detail = 'paused by operator'")


def downgrade() -> None:
    op.drop_column("projects", "is_paused")
