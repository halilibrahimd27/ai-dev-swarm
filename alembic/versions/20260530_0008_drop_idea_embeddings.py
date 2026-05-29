"""Drop the unused idea_embeddings (pgvector) table.

ARCHITECTURE §5.7 originally specified pgvector cosine dedup of shipped
project specs. That path was never wired — ``PgvectorMemory`` was
instantiated and discarded, no embeddings were ever generated, and the
``idea_embeddings`` table stayed empty. Idea dedup against the swarm's
own history is now done with cheap title+summary token similarity
(``SelfHistoryDedup``), so the table is dead weight.

This drops it (and its ivfflat index). The ``vector`` extension is left
installed — it's harmless and re-adding it on downgrade would need
superuser. Downgrade re-creates the table so the migration is reversible.

Revision ID: 20260530_0008
Revises: 20260529_0007
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260530_0008"
down_revision: str | Sequence[str] | None = "20260529_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idea_embeddings_cosine_idx")
    op.execute("DROP TABLE IF EXISTS idea_embeddings")


def downgrade() -> None:
    # Re-create the original shape (requires the `vector` extension, which
    # the Phase-0 init.sql installs and we never drop).
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS idea_embeddings (
            project_id    uuid        PRIMARY KEY REFERENCES projects (id) ON DELETE CASCADE,
            embedding     vector(1536) NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idea_embeddings_cosine_idx
            ON idea_embeddings USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 100)
        """
    )
