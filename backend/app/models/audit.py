"""The tamper-evident audit chain (S11)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UtcDateTime

GENESIS_HASH = "0" * 64


class AuditRecord(Base):
    """One audit event. ``hash`` covers every field plus ``prev_hash`` of the same chain."""

    __tablename__ = "audit_chain"
    __table_args__ = (UniqueConstraint("chain_id", "seq", name="uq_audit_chain_position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chain_id: Mapped[str] = mapped_column(String(40), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    ts: Mapped[datetime] = mapped_column(UtcDateTime)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True
    )
    actor_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    event: Mapped[str] = mapped_column(String(60))
    detail: Mapped[str] = mapped_column(Text, default="{}")
    request_id: Mapped[str] = mapped_column(String(64), default="-")
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)
