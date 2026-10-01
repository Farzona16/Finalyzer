"""Access JWTs and refresh tokens (S3)."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

import jwt

from app import clock
from app.config import BackendSettings
from app.models import User

ALGORITHM = "HS256"
ISSUER = "finalyzer"
AUDIENCE = "finalyzer-web"


@dataclass(frozen=True)
class AccessClaims:
    """The verified content of an access token."""

    user_id: str
    workspace_id: str
    role: str
    token_version: int
    expires_at: datetime


def create_access_token(settings: BackendSettings, user: User) -> tuple[str, datetime]:
    """Sign a short-lived access token. Returns the token and its expiry."""
    issued = clock.now()
    expires = issued + timedelta(minutes=settings.access_token_minutes)
    payload = {
        "sub": user.id,
        "wid": user.workspace_id,
        "role": user.role,
        "tv": user.token_version,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": secrets.token_hex(8),
    }
    return jwt.encode(payload, settings.app_secret_key, algorithm=ALGORITHM), expires


def decode_access_token(settings: BackendSettings, token: str) -> AccessClaims | None:
    """Verify signature, issuer, audience and expiry. Returns ``None`` for any problem.

    Expiry is checked against our own clock so tests can move time.
    """
    try:
        claims = jwt.decode(
            token,
            settings.app_secret_key,
            algorithms=[ALGORITHM],  # fixed list: never trust the token header
            issuer=ISSUER,
            audience=AUDIENCE,
            options={"require": ["exp", "iat", "sub", "wid", "role", "tv"], "verify_exp": False},
        )
        expires = datetime.fromtimestamp(int(claims["exp"]), tz=clock.now().tzinfo)
        if expires <= clock.now():
            return None
        return AccessClaims(
            user_id=str(claims["sub"]),
            workspace_id=str(claims["wid"]),
            role=str(claims["role"]),
            token_version=int(claims["tv"]),
            expires_at=expires,
        )
    except (jwt.PyJWTError, ValueError, TypeError):
        return None


def new_refresh_token() -> tuple[str, str]:
    """A random 256-bit token and its SHA-256 hash. Only the hash is stored."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    """Hash a refresh token for lookup (the token is random, so no salt is needed)."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
