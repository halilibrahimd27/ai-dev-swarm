"""idea_evaluations + projects.status_detail.

Persists every scored idea the Ideation crew produces (per round) with
its rubric breakdown, novelty verdict, and accept/reject reason so the
control plane can SHOW *why* a project was started or an idea dropped.
Adds ``projects.status_detail`` so a blocked/paused project can carry a
human-readable reason ("why is it stuck?").

Revision ID: 20260529_0003
Revises: 20260525_0002
Create Date: 2026-05-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260529_0003"
down_revision: str | Sequence[str] | None = "20260525_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "idea_evaluations",
        sa.Column("id", sa.BigInteger, sa.Identity(start=1, cycle=False), primary_key=True),
        sa.Column("round", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("summary", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("scores", postgresql.JSONB, nullable=False),
        sa.Column("total", sa.Integer, nullable=False),
        sa.Column("novel", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("accepted", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("rejected_reason", sa.Text, nullable=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idea_evaluations_created_idx", "idea_evaluations", [sa.text("created_at DESC")]
    )
    op.add_column("projects", sa.Column("status_detail", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "status_detail")
    op.drop_index("idea_evaluations_created_idx", table_name="idea_evaluations")
    op.drop_table("idea_evaluations")
