"""milestone_sessions — Phase 2 mandate.

Persists the Claude Agent SDK ``ResultMessage`` of every SDK
invocation: ``session_id``, ``total_cost_usd``, ``num_turns``. On
retry of a failed milestone the build crew reads the most recent row
for ``(milestone_id, role)`` and passes ``resume=session_id`` into the
SDK so the conversation continues instead of restarting.

Revision ID: 20260525_0002
Revises: 20260525_0001
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260525_0002"
down_revision: str | Sequence[str] | None = "20260525_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "milestone_sessions",
        sa.Column(
            "id",
            sa.BigInteger,
            sa.Identity(start=1, cycle=False),
            primary_key=True,
        ),
        sa.Column(
            "milestone_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("milestones.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("session_id", sa.Text, nullable=False),
        sa.Column("cost_usd", sa.Numeric(10, 4), nullable=False),
        sa.Column("turns", sa.Integer, nullable=False),
        sa.Column(
            "finished_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "milestone_sessions_latest_idx",
        "milestone_sessions",
        ["milestone_id", "role", sa.text("finished_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("milestone_sessions_latest_idx", table_name="milestone_sessions")
    op.drop_table("milestone_sessions")
