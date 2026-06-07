"""steering per-role delivery — honour the documented "once per role" intent.

The Phase-1 ``steering_notes`` design promised "delivered exactly once per
role", but the implementation dropped the target role at write and ignored
it at read (``pull_unconsumed`` consumed every note project-wide, so the
first role to pull starved the others). This migration adds:

  * ``steering_notes.target_role`` — NULL = visible to ALL roles; else only
    that role receives the note.
  * ``steering_note_deliveries(note_id, role)`` — a per-(note, role) ledger
    so a broadcast (NULL) note reaches EACH role exactly once, and a
    targeted note reaches its role exactly once. Delivery is now tracked
    per role instead of a single project-wide ``consumed_at`` flag.

The legacy ``consumed_at`` / ``consumed_by`` columns are LEFT in place
(harmless) — the new read path uses the deliveries ledger.

Revision ID: 20260607_0009
Revises: 20260530_0008
Create Date: 2026-06-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260607_0009"
down_revision: str | Sequence[str] | None = "20260530_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "steering_notes",
        sa.Column("target_role", sa.Text, nullable=True),
    )
    op.create_table(
        "steering_note_deliveries",
        sa.Column(
            "note_id",
            sa.BigInteger,
            sa.ForeignKey("steering_notes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column(
            "delivered_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("note_id", "role", name="steering_note_deliveries_pk"),
    )


def downgrade() -> None:
    op.drop_table("steering_note_deliveries")
    op.drop_column("steering_notes", "target_role")
