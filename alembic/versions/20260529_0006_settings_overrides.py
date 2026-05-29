"""settings_overrides — operator-editable operational knobs.

A small key/value table holding overrides for a CURATED set of
operational settings (budgets, caps, modes, flags) that the operator can
change from the web UI without editing ``.env`` + restarting. Secrets,
credentials, hosts and pool sizes are NEVER stored here — only the
allow-listed keys in ``db.settings_store.EDITABLE_SETTINGS``. Overrides
are applied onto the live ``Settings`` object at startup and (for
live-readable keys) immediately when changed.

Revision ID: 20260529_0006
Revises: 20260529_0005
Create Date: 2026-05-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260529_0006"
down_revision: str | Sequence[str] | None = "20260529_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "settings_overrides",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("settings_overrides")
