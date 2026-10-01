"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-01 21:03:33.952742
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply this revision."""
    op.create_table(
        "login_throttle",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_login_throttle")),
    )
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("privacy_mode", sa.String(length=10), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("language", sa.String(length=5), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspaces")),
    )
    op.create_table(
        "audit_chain",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chain_id", sa.String(length=40), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=True),
        sa.Column("actor_user_id", sa.String(length=32), nullable=True),
        sa.Column("event", sa.String(length=60), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_audit_chain_workspace_id_workspaces"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_chain")),
        sa.UniqueConstraint("chain_id", "seq", name="uq_audit_chain_position"),
        sa.UniqueConstraint("hash", name=op.f("uq_audit_chain_hash")),
    )
    with op.batch_alter_table("audit_chain", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_audit_chain_chain_id"), ["chain_id"], unique=False)

    op.create_table(
        "datasets",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("domain", sa.String(length=30), nullable=True),
        sa.Column("storage_name", sa.String(length=64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("model_json", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_datasets_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_datasets")),
        sa.UniqueConstraint("storage_name", name=op.f("uq_datasets_storage_name")),
    )
    with op.batch_alter_table("datasets", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_datasets_workspace_id"), ["workspace_id"], unique=False
        )

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("message_key", sa.String(length=80), nullable=False),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_jobs_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_jobs_workspace_id"), ["workspace_id"], unique=False)

    op.create_table(
        "users",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workspace_id", sa.String(length=32), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=10), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=5), nullable=False),
        sa.Column("theme", sa.String(length=10), nullable=False),
        sa.Column("reading_level", sa.String(length=10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "reading_level in ('simple','expert')", name=op.f("ck_users_reading_level_known")
        ),
        sa.CheckConstraint(
            "role in ('admin','analyst','viewer')", name=op.f("ck_users_role_known")
        ),
        sa.CheckConstraint("theme in ('light','dark','system')", name=op.f("ck_users_theme_known")),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_users_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_users_workspace_id"), ["workspace_id"], unique=False)

    op.create_table(
        "dashboards",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("dataset_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("layout_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name=op.f("fk_dashboards_dataset_id_datasets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dashboards")),
        sa.UniqueConstraint("dataset_id", "user_id", name="uq_dashboards_owner"),
    )
    with op.batch_alter_table("dashboards", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_dashboards_dataset_id"), ["dataset_id"], unique=False)

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("family_id", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_refresh_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_tokens_token_hash")),
    )
    with op.batch_alter_table("refresh_tokens", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_refresh_tokens_family_id"), ["family_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_refresh_tokens_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    """Undo this revision."""
    with op.batch_alter_table("refresh_tokens", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_refresh_tokens_user_id"))
        batch_op.drop_index(batch_op.f("ix_refresh_tokens_family_id"))

    op.drop_table("refresh_tokens")
    with op.batch_alter_table("dashboards", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_dashboards_dataset_id"))

    op.drop_table("dashboards")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_workspace_id"))

    op.drop_table("users")
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_jobs_workspace_id"))

    op.drop_table("jobs")
    with op.batch_alter_table("datasets", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_datasets_workspace_id"))

    op.drop_table("datasets")
    with op.batch_alter_table("audit_chain", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_chain_chain_id"))

    op.drop_table("audit_chain")
    op.drop_table("workspaces")
    op.drop_table("login_throttle")
