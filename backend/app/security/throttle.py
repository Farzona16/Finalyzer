"""Brute-force protection: exponential lockout per account and per IP address (S2).

Accounts are keyed by a hash of the e-mail whether or not the user exists, so the error
text and the timing never reveal which e-mails are registered.
"""

from __future__ import annotations

import hashlib
import math
from datetime import timedelta

from sqlalchemy.orm import Session

from app import clock
from app.errors import AppError
from app.models import LoginThrottle

FREE_FAILURES = 5
MAX_LOCK_MINUTES = 30
DECAY_AFTER_MINUTES = 60  # a counter is forgotten after this long without a failure


def lock_minutes(failures: int) -> int:
    """Lock length after ``failures`` failed attempts: 0 below 5, then 1, 2, 4, ... up to 30."""
    if failures < FREE_FAILURES:
        return 0
    return min(2 ** (failures - FREE_FAILURES), MAX_LOCK_MINUTES)


def throttle_keys(email: str, ip: str) -> tuple[str, str]:
    """The account key and the address key for one login attempt."""
    digest = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:32]
    return f"user:{digest}", f"ip:{ip[:60]}"


def ensure_not_locked(db: Session, keys: tuple[str, ...]) -> None:
    """Raise ``account_locked`` (with ``Retry-After``) while any key is locked."""
    now = clock.now()
    for key in keys:
        row = db.get(LoginThrottle, key)
        if row and row.locked_until and row.locked_until > now:
            wait = max(1, math.ceil((row.locked_until - now).total_seconds()))
            raise AppError("account_locked", headers={"Retry-After": str(wait)})


def record_failure(db: Session, keys: tuple[str, ...]) -> None:
    """Count one failed attempt on every key and start a lock when the limit is reached."""
    now = clock.now()
    for key in keys:
        row = db.get(LoginThrottle, key)
        if row is None:
            row = LoginThrottle(key=key, failures=0, updated_at=now)
            db.add(row)
        quiet = now - row.updated_at >= timedelta(minutes=DECAY_AFTER_MINUTES)
        if quiet and not (row.locked_until and row.locked_until > now):
            row.failures = 0  # old typos must not add up to a lock days later
        row.failures += 1
        row.updated_at = now
        minutes = lock_minutes(row.failures)
        row.locked_until = now + timedelta(minutes=minutes) if minutes else None
    db.flush()


def record_success(db: Session, account_key: str) -> None:
    """A correct password clears the account counter (the address counter is kept)."""
    row = db.get(LoginThrottle, account_key)
    if row is not None:
        row.failures = 0
        row.locked_until = None
        db.flush()
