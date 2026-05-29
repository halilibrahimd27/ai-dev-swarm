"""transcript_entries — durable inter-agent transcript.

The live transcript used to exist ONLY in per-connection asyncio queues
(``EventBridge``): a page refresh lost everything, and history before a
client connected was gone. This table persists every transcript entry so
the web UI can load a project's FULL conversation on open and it survives
refreshes for the life of the project. ``seq`` (bigserial) gives a stable
chronological order; rows cascade-delete with their project.

Revision ID: 20260529_0005
Revises: 20260529_0004
Create Date: 2026-05-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260529_0005"
down_revision: str | Sequence[str] | None = "20260529_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "transcript_entries",
        sa.Column(
            "seq",
            sa.BigInteger,
            sa.Identity(start=1, cycle=False),
            primary_key=True,
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("role", sa.Text, nullable=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "extra",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "transcript_project_seq_idx",
        "transcript_entries",
        ["project_id", "seq"],
    )


def downgrade() -> None:
    op.drop_index("transcript_project_seq_idx", table_name="transcript_entries")
    op.drop_table("transcript_entries")
