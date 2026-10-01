"""datasets.last_accessed_at (idle purge of ephemeral data)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-01 23:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    with op.batch_alter_table("datasets", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "last_accessed_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )


def downgrade() -> None:
    """Undo this revision."""
    with op.batch_alter_table("datasets", schema=None) as batch_op:
        batch_op.drop_column("last_accessed_at")
