"""Password policy and argon2id hashing (S1)."""

from __future__ import annotations

import time
from functools import lru_cache
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.errors import AppError

MIN_LENGTH = 12
MAX_LENGTH = 128  # a cap stops someone from sending megabytes into the hash function
COMMON_PASSWORDS_FILE = Path(__file__).with_name("common_passwords.txt")
CALIBRATION_TARGET_MS = 250
MAX_TIME_COST = 6


@lru_cache(maxsize=1)
def common_passwords() -> frozenset[str]:
    """The embedded list of the 10,000 most common passwords (lower-case)."""
    text = COMMON_PASSWORDS_FILE.read_text(encoding="utf-8", errors="ignore")
    return frozenset(line.strip().lower() for line in text.splitlines() if line.strip())


def is_common(password: str) -> bool:
    """True for a listed password, also when it only adds digits or symbols at the end.

    The embedded list holds only 10 entries of 12+ characters, so "password1234" would
    pass a plain lookup; stripping the tail catches that very common pattern.
    """
    lowered = password.strip().lower()
    base = lowered.rstrip("0123456789!@#$%^&*()-_=+.,?")
    return lowered in common_passwords() or (len(base) >= 4 and base in common_passwords())


def check_password_policy(password: str, email: str | None = None) -> None:
    """Raise an ``AppError`` naming the first broken rule."""
    if len(password) < MIN_LENGTH:
        raise AppError("password_too_short", params={"min": MIN_LENGTH})
    if len(password) > MAX_LENGTH:
        raise AppError("password_too_long", params={"max": MAX_LENGTH})
    if email and password.strip().lower() == email.strip().lower():
        raise AppError("password_equals_email")
    if is_common(password):
        raise AppError("password_too_common")


def calibrated_hasher(target_ms: int = CALIBRATION_TARGET_MS) -> PasswordHasher:
    """Pick the smallest argon2id time cost that makes one hash take about ``target_ms``.

    Memory stays at 64 MiB so a small hosting plan is not exhausted by parallel logins.
    """
    for time_cost in range(2, MAX_TIME_COST + 1):
        hasher = PasswordHasher(time_cost=time_cost, memory_cost=65536, parallelism=2)
        started = time.perf_counter()
        hasher.hash("calibration-only")
        if (time.perf_counter() - started) * 1000 >= target_ms:
            return hasher
    return PasswordHasher(time_cost=MAX_TIME_COST, memory_cost=65536, parallelism=2)


def fast_hasher() -> PasswordHasher:
    """Cheap parameters for the test suite only."""
    return PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)


class PasswordService:
    """Hash and verify passwords; keeps one dummy hash to equalize timing for unknown users."""

    def __init__(self, hasher: PasswordHasher) -> None:
        self.hasher = hasher
        self._dummy = hasher.hash("not-a-real-password-for-timing-only")

    def hash(self, password: str) -> str:
        """Hash a password with a fresh random salt."""
        return self.hasher.hash(password)

    def verify(self, password_hash: str | None, password: str) -> bool:
        """Check a password. With no hash (unknown user) a dummy check keeps timing similar."""
        try:
            return self.hasher.verify(password_hash or self._dummy, password) and bool(
                password_hash
            )
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        """True when stored parameters are weaker than the current ones."""
        return self.hasher.check_needs_rehash(password_hash)
