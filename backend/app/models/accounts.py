"""Workspaces, users, refresh tokens and login throttling."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app import clock
from app.models.base import Base, UtcDateTime, new_id

ROLES = ("admin", "analyst", "viewer")
THEMES = ("light", "dark", "system")
READING_LEVELS = ("simple", "expert")
CURRENCIES = ("UZS", "USD", "EUR", "RUB")


class Workspace(Base):
    """One organization. Every other row is scoped to a workspace (decision D5)."""

    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    privacy_mode: Mapped[str] = mapped_column(String(10), default="demo")
    currency: Mapped[str] = mapped_column(String(3), default="UZS")
    language: Mapped[str] = mapped_column(String(5), default="uz")
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=clock.now)

    users: Mapped[list[User]] = relationship(back_populates="workspace")


class User(Base):
    """A person who can sign in. ``token_version`` invalidates access tokens when it changes."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role in ('admin','analyst','viewer')", name="role_known"),
        CheckConstraint("theme in ('light','dark','system')", name="theme_known"),
        CheckConstraint("reading_level in ('simple','expert')", name="reading_level_known"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(254), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(10), default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    language: Mapped[str] = mapped_column(String(5), default="uz")
    theme: Mapped[str] = mapped_column(String(10), default="system")
    reading_level: Mapped[str] = mapped_column(String(10), default="simple")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=clock.now)
    password_changed_at: Mapped[datetime] = mapped_column(UtcDateTime, default=clock.now)
    last_login_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    workspace: Mapped[Workspace] = relationship(back_populates="users")


class RefreshToken(Base):
    """A hashed refresh token. Tokens of one login chain share a ``family_id`` (S3)."""

    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[str] = mapped_column(String(32), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=clock.now)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class LoginThrottle(Base):
    """Failed-login counter per key (``user:<email hash>`` or ``ip:<address>``), see S2."""

    __tablename__ = "login_throttle"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=clock.now)
