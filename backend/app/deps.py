"""FastAPI dependencies: settings, database session, client address, current user, CSRF, limits."""

from __future__ import annotations

from collections.abc import Callable, Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.config import BackendSettings
from app.errors import AppError
from app.models import User
from app.security import csrf as csrf_module
from app.security.cookies import ACCESS_COOKIE
from app.security.passwords import PasswordService
from app.security.permissions import Permission, has_permission
from app.security.ratelimit import RateLimiter
from app.security.tokens import decode_access_token

# Routes an anonymous visitor may call. A test compares this set with the OpenAPI document,
# so a new public route must be listed here on purpose (S6).
PUBLIC_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/v1/healthz"),
        ("GET", "/api/v1/readyz"),
        ("GET", "/api/v1/version"),
        ("GET", "/api/v1/auth/csrf"),
        ("POST", "/api/v1/auth/first-admin"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/refresh"),
    }
)


def get_app_settings(request: Request) -> BackendSettings:
    """Settings of the running app."""
    return request.app.state.settings


def get_limiter(request: Request) -> RateLimiter:
    """The shared rate limiter."""
    return request.app.state.limiter


def get_passwords(request: Request) -> PasswordService:
    """The shared password hasher."""
    return request.app.state.passwords


def get_db(request: Request) -> Iterator[Session]:
    """One session per request. Services commit explicitly; anything left is rolled back."""
    session: Session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def client_ip(request: Request, settings: BackendSettings) -> str:
    """The caller's address; ``X-Forwarded-For`` is used only behind a trusted proxy."""
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        parts = [part.strip() for part in forwarded.split(",") if part.strip()]
        if parts:
            return parts[-1]  # the entry added by our own proxy, not one the client can forge
    return request.client.host if request.client else "unknown"


def csrf_protect(request: Request, settings: BackendSettings = Depends(get_app_settings)) -> None:
    """Global dependency: unsafe methods need a valid CSRF token (S5)."""
    csrf_module.verify_request(request, settings.app_secret_key)


def current_user(
    request: Request,
    db: Session = Depends(get_db),
    settings: BackendSettings = Depends(get_app_settings),
) -> User:
    """The signed-in, active user behind a valid access token; otherwise 401."""
    token = request.cookies.get(ACCESS_COOKIE)
    claims = decode_access_token(settings, token) if token else None
    if claims is None:
        raise AppError("not_authenticated")
    user = db.get(User, claims.user_id)
    if (
        user is None
        or not user.is_active
        or user.token_version != claims.token_version
        or user.workspace_id != claims.workspace_id
    ):
        raise AppError("not_authenticated")
    return user


def require(permission: Permission) -> Callable[..., User]:
    """Dependency factory: the user must hold ``permission`` (403 otherwise)."""

    def check(user: User = Depends(current_user)) -> User:
        if not has_permission(user.role, permission):
            raise AppError("forbidden")
        return user

    return check


def limit(policy_name: str) -> Callable[..., None]:
    """Dependency factory for the per-route policies (login, upload, ask)."""

    def check(
        request: Request,
        settings: BackendSettings = Depends(get_app_settings),
        limiter: RateLimiter = Depends(get_limiter),
    ) -> None:
        token = request.cookies.get(ACCESS_COOKIE)
        claims = decode_access_token(settings, token) if token else None
        if policy_name == "login" or claims is None:
            # Sign-in attempts are always counted per address: holding a valid cookie of
            # one's own account must not give extra attempts against other accounts.
            key = f"ip:{client_ip(request, settings)}"
        else:
            key = f"user:{claims.user_id}"
        limiter.check(policy_name, key)

    return check


# Global dependency: 120 requests per minute per user (per address when anonymous).
api_rate_limit = limit("api")
