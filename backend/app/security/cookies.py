"""Cookie names and attributes (S4)."""

from __future__ import annotations

from datetime import datetime

from fastapi import Response

from app import clock
from app.config import BackendSettings
from app.security.csrf import CSRF_COOKIE

ACCESS_COOKIE = "fz_access"
REFRESH_COOKIE = "fz_refresh"
API_PREFIX = "/api/v1"
REFRESH_PATH = f"{API_PREFIX}/auth"  # the refresh cookie is only sent to auth endpoints


def set_access_cookie(
    response: Response, settings: BackendSettings, token: str, expires: datetime
) -> None:
    """Store the access token (httpOnly, SameSite=Lax, Secure outside local dev)."""
    response.set_cookie(
        ACCESS_COOKIE,
        token,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path=API_PREFIX,
        max_age=max(1, int((expires - clock.now()).total_seconds())),
    )


def set_refresh_cookie(response: Response, settings: BackendSettings, token: str) -> None:
    """Store the refresh token with a narrow path."""
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path=REFRESH_PATH,
        max_age=settings.refresh_token_days * 86400,
    )


def set_csrf_cookie(response: Response, settings: BackendSettings, token: str) -> None:
    """Store the CSRF token. It is also returned in the body, so the cookie can be httpOnly."""
    response.set_cookie(
        CSRF_COOKIE,
        token,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )


def clear_auth_cookies(response: Response, settings: BackendSettings) -> None:
    """Remove both session cookies."""
    for name, path in ((ACCESS_COOKIE, API_PREFIX), (REFRESH_COOKIE, REFRESH_PATH)):
        response.delete_cookie(
            name, path=path, httponly=True, secure=settings.secure_cookies, samesite="lax"
        )
