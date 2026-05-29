"""Make milestones (project_id, ordinal) UNIQUE deferrable.

The replanner shifts milestone ordinals in bulk (Split/insert_after do
``UPDATE milestones SET ordinal = ordinal + 1 WHERE ordinal > N``).
Postgres checks a plain UNIQUE constraint row-by-row DURING the
statement, so the shift transiently collides (e.g. row 1->2 while a
row 2 still exists) and raises UniqueViolation — which crashed the
project in REPLANNING the moment auto-split tried to bisect an oversized
milestone. Making the constraint DEFERRABLE INITIALLY DEFERRED moves the
check to COMMIT, by which point the ordinals are consistent again.

Revision ID: 20260529_0004
Revises: 20260529_0003
Create Date: 2026-05-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260529_0004"
down_revision: str | Sequence[str] | None = "20260529_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "milestones_project_id_ordinal_key"


def upgrade() -> None:
    # Drop the auto-named UNIQUE (created by init.sql / earlier DDL) and
    # re-add it deferrable. IF EXISTS keeps this idempotent across the
    # init.sql + migration ordering on fresh clones.
    op.execute(f"ALTER TABLE milestones DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
    op.execute(
        f"ALTER TABLE milestones ADD CONSTRAINT {_CONSTRAINT} "
        "UNIQUE (project_id, ordinal) DEFERRABLE INITIALLY DEFERRED"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE milestones DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
    op.execute(f"ALTER TABLE milestones ADD CONSTRAINT {_CONSTRAINT} UNIQUE (project_id, ordinal)")
