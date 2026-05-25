"""steering_notes — Phase 1 mandate.

Operator-visible notes that the orchestrator injects into every CrewAI
role's system prompt via the ``{steering_notes}`` slot. Notes are
"consumed" the first time they appear in a rendered prompt, so the same
note is delivered exactly once per role.

Revision ID: 20260525_0001
Revises:
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260525_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "steering_notes",
        sa.Column(
            "id",
            sa.BigInteger,
            sa.Identity(start=1, cycle=False),
            primary_key=True,
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("author", sa.Text, nullable=False, server_default=sa.text("'human'")),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("consumed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("consumed_by", sa.Text, nullable=True),
    )
    op.create_index(
        "steering_notes_unconsumed_idx",
        "steering_notes",
        ["project_id", "consumed_at"],
    )


def downgrade() -> None:
    op.drop_index("steering_notes_unconsumed_idx", table_name="steering_notes")
    op.drop_table("steering_notes")
