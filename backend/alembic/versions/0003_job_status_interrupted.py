"""jobs.status wide enough for ``interrupted``

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-09 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=10),
            type_=sa.String(length=16),
            existing_nullable=False,
        )


def downgrade() -> None:
    """Undo this revision."""
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=16),
            type_=sa.String(length=10),
            existing_nullable=False,
        )
