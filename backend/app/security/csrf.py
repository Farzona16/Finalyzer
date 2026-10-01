"""CSRF protection: signed double-submit token plus an Origin check (S5)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from urllib.parse import urlsplit

from starlette.requests import Request

from app.errors import AppError

CSRF_COOKIE = "fz_csrf"
CSRF_HEADER = "x-csrf-token"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _sign(secret: str, nonce: str) -> str:
    """HMAC of the nonce, so a cookie planted from a sibling domain is not accepted."""
    return hmac.new(secret.encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256).hexdigest()


def issue_token(secret: str) -> str:
    """A fresh token: ``<random>.<signature>``."""
    nonce = secrets.token_urlsafe(24)
    return f"{nonce}.{_sign(secret, nonce)}"


def is_valid_token(secret: str, token: str) -> bool:
    """True when the token has the right shape and a correct signature."""
    nonce, _, signature = token.partition(".")
    if not nonce or not signature:
        return False
    return hmac.compare_digest(signature, _sign(secret, nonce))


def origin_is_allowed(request: Request) -> bool:
    """Same-origin check. A missing Origin header is allowed (non-browser clients).

    Only the host part is compared, because a TLS-terminating proxy may rewrite the scheme.
    """
    origin = request.headers.get("origin")
    if origin is None:
        return True
    if origin == "null":
        return False
    return urlsplit(origin).netloc.lower() == request.headers.get("host", "").lower()


def verify_request(request: Request, secret: str) -> None:
    """Raise ``AppError`` unless an unsafe request carries a valid, matching token."""
    if request.method not in UNSAFE_METHODS:
        return
    if not origin_is_allowed(request):
        raise AppError("origin_not_allowed")
    header = request.headers.get(CSRF_HEADER, "")
    cookie = request.cookies.get(CSRF_COOKIE, "")
    matches = bool(header) and hmac.compare_digest(header, cookie)
    if not (matches and is_valid_token(secret, header)):
        raise AppError("csrf_failed")
