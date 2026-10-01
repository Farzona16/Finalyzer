"""One place for "now", so tests can move time without sleeping."""

from __future__ import annotations

import time
from datetime import UTC, datetime


def now() -> datetime:
    """Current time as an aware UTC datetime."""
    return datetime.now(UTC)


def monotonic() -> float:
    """Monotonic seconds, used by the in-memory rate limiter."""
    return time.monotonic()


def as_utc(value: datetime) -> datetime:
    """Treat a naive datetime read from SQLite as UTC; leave aware values alone."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)
