"""Declarative base, naming convention and shared column helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

# Named constraints make Alembic migrations (especially on SQLite) predictable.
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class of all tables."""

    metadata = MetaData(naming_convention=NAMING)


class UtcDateTime(TypeDecorator[datetime]):
    """Stores UTC and always returns aware UTC datetimes (SQLite drops the zone)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Convert to UTC before writing; refuse naive values (they hide bugs)."""
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime given; use timezone-aware UTC values")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Attach UTC to what the database returns."""
        if value is None:
            return None
        return value if value.tzinfo else value.replace(tzinfo=UTC)


def new_id() -> str:
    """A random 32-character hex id (never derived from user input)."""
    return uuid.uuid4().hex
