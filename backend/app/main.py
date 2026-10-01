"""FastAPI application factory.

Run with: ``uvicorn app.main:create_app --factory --port 8000`` (one worker, decision D3).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI

from app import __version__
from app.config import BackendSettings, get_settings
from app.db import make_engine, make_session_factory, upgrade_to_head
from app.deps import api_rate_limit, csrf_protect
from app.errors import install_error_handlers
from app.logging_setup import setup_logging
from app.routers import admin, auth, meta
from app.security.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from app.security.passwords import PasswordService, calibrated_hasher, fast_hasher
from app.security.ratelimit import RateLimiter, policies_from
from app.spa import install_spa

API_PREFIX = "/api/v1"


def build_api_router() -> APIRouter:
    """All `/api/v1` routes. CSRF and the per-user limit apply to every one of them."""
    api = APIRouter(
        prefix=API_PREFIX, dependencies=[Depends(csrf_protect), Depends(api_rate_limit)]
    )
    api.include_router(meta.router)
    api.include_router(auth.router)
    api.include_router(admin.router)
    return api


def create_app(settings: BackendSettings | None = None) -> FastAPI:
    """Build the app. Fails closed if the settings are invalid (S15)."""
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    engine = make_engine(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if settings.auto_migrate:
            upgrade_to_head(engine)
        yield
        engine.dispose()

    app = FastAPI(
        title="Finalyzer",
        version=__version__,
        lifespan=lifespan,
        # The default Swagger page loads scripts from a CDN, which our CSP forbids (D2).
        docs_url=None,
        redoc_url=None,
        openapi_url=None if settings.is_production else f"{API_PREFIX}/openapi.json",
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.limiter = RateLimiter(policies_from(settings))
    app.state.passwords = PasswordService(
        fast_hasher() if settings.app_env == "test" else calibrated_hasher()
    )

    # Added last = outermost: headers wrap everything, including the context middleware's 500s.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    install_error_handlers(app)

    app.include_router(build_api_router())
    app.add_api_route("/healthz", meta.root_healthz, methods=["GET"], include_in_schema=False)
    install_spa(app, settings.frontend_dist)
    return app
