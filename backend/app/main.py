"""FastAPI application factory.

Run with: ``uvicorn app.main:create_app --factory --port 8000`` (one worker, decision D3).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI

from app import __version__
from app.config import BackendSettings, get_settings
from app.db import make_engine, make_session_factory, session_scope, upgrade_to_head
from app.deps import api_rate_limit, csrf_protect
from app.errors import install_error_handlers
from app.jobs.runner import JobRunner
from app.logging_setup import setup_logging
from app.routers import admin, auth, datasets, jobs, meta
from app.security.bodylimit import BodyLimitMiddleware
from app.security.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from app.security.passwords import PasswordService, calibrated_hasher, fast_hasher
from app.security.ratelimit import RateLimiter, policies_from
from app.services import datasets as dataset_service
from app.services.datasets import Services
from app.services.engine import core_settings
from app.spa import install_spa
from app.storage.handles import HandleCache
from app.storage.store import DatasetStore

API_PREFIX = "/api/v1"
logger = logging.getLogger("finalyzer.main")


def build_api_router() -> APIRouter:
    """All `/api/v1` routes. CSRF and the per-user limit apply to every one of them."""
    api = APIRouter(
        prefix=API_PREFIX, dependencies=[Depends(csrf_protect), Depends(api_rate_limit)]
    )
    api.include_router(meta.router)
    api.include_router(auth.router)
    api.include_router(admin.router)
    api.include_router(datasets.router)
    api.include_router(datasets.demo_router)
    api.include_router(jobs.router)
    return api


async def sweep_forever(services: Services, interval: int) -> None:
    """Delete idle and expired datasets every ``interval`` seconds (S17)."""
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(sweep_once, services)
        except Exception as exc:  # noqa: BLE001 - the sweep must never stop the app
            logger.error("sweep_failed error=%s", type(exc).__name__)


def sweep_once(services: Services) -> int:
    """One sweep with its own database session."""
    with session_scope(services.factory) as db:
        return dataset_service.purge_expired(services, db)


def create_app(settings: BackendSettings | None = None) -> FastAPI:
    """Build the app. Fails closed if the settings are invalid (S15)."""
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    engine = make_engine(settings)

    session_factory = make_session_factory(engine)
    store = DatasetStore(settings)
    handles = HandleCache(
        store, lambda: core_settings(settings), max_open=settings.max_open_datasets
    )
    services = Services(settings, session_factory, store, handles)
    runner = JobRunner(session_factory, workers=settings.job_workers)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if settings.auto_migrate:
            upgrade_to_head(engine)
        with session_scope(session_factory) as db:
            dataset_service.reset_after_restart(services, db)
        sweeper = (
            asyncio.create_task(sweep_forever(services, settings.purge_interval_seconds))
            if settings.purge_interval_seconds > 0
            else None
        )
        yield
        if sweeper is not None:
            sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper
        runner.shutdown()
        handles.close_all()
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
    app.state.session_factory = session_factory
    app.state.services = services
    app.state.jobs = runner
    app.state.limiter = RateLimiter(policies_from(settings))
    app.state.passwords = PasswordService(
        fast_hasher() if settings.app_env == "test" else calibrated_hasher()
    )

    # Added last = outermost: headers wrap everything, including the context middleware's 500s.
    app.add_middleware(
        BodyLimitMiddleware,
        prefixes=(f"{API_PREFIX}/datasets",),
        limit_bytes=settings.max_request_mb * 1024 * 1024,
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    install_error_handlers(app)

    app.include_router(build_api_router())
    app.add_api_route("/healthz", meta.root_healthz, methods=["GET"], include_in_schema=False)
    install_spa(app, settings.frontend_dist)
    return app
