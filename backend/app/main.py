"""FastAPI application factory.

Run with ``.\\start.ps1`` or ``python run_backend.py`` from the repo root so the
backend virtualenv is used. Direct form (from ``backend/``, that venv active):

``python -m uvicorn app.main:create_app --factory --reload --port 8000``

There is no module-level ``app`` object (settings fail closed at import). One
Uvicorn worker in production (decision D3).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

try:
    from fastapi import APIRouter, Depends, FastAPI
except ModuleNotFoundError as exc:  # wrong interpreter, typically the root .venv
    raise ModuleNotFoundError(
        "fastapi is not installed in this Python. On Windows run .\\start.ps1 "
        "or: backend\\.venv\\Scripts\\python.exe run_backend.py"
    ) from exc

from app import __version__
from app.config import BackendSettings, get_settings
from app.db import make_engine, make_session_factory, session_scope, upgrade_to_head
from app.deps import api_rate_limit, csrf_protect
from app.errors import install_error_handlers
from app.jobs.runner import JobRunner
from app.logging_setup import setup_logging
from app.routers import admin, ask, auth, dashboard, datasets, insights, jobs, kpis, meta, privacy
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
from core.llm.gateway import clear_prompt_cache
from core.llm.registry import verify_models
from core.runtime.health import HealthShortcut
from core.runtime.tile_cache import clear as clear_tile_cache
from core.runtime.watchdog import register_cache_clear

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
    api.include_router(kpis.router)
    api.include_router(ask.router)
    api.include_router(dashboard.router)
    api.include_router(insights.router)
    api.include_router(privacy.router)
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


def check_models(services: Services) -> None:
    """Compare configured model ids with each provider's model list (spec 3.2, R13).

    Unknown ids skip that provider; a failed check (no network) leaves it unchecked.
    """
    settings = core_settings(services.settings)
    if settings.effective_llm_mode == "offline":
        return
    try:
        runtime = services.llm_runtime
        verify_models(runtime.providers(settings), runtime)
    except Exception as exc:  # noqa: BLE001 - startup must never fail on a model check
        logger.warning("model_check_failed error=%s", type(exc).__name__)


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
        if settings.app_env != "test":
            threading.Thread(
                target=check_models, args=(services,), name="model-check", daemon=True
            ).start()
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

    # Added last = outermost. HealthShortcut must sit outside so keep-alives skip FastAPI.
    app.add_middleware(HealthShortcut)
    # Headers wrap everything, including the context middleware's 500s.
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
    app.add_api_route("/health/ready", meta.root_ready, methods=["GET"], include_in_schema=False)
    install_spa(app, settings.frontend_dist)
    register_cache_clear(clear_prompt_cache)
    register_cache_clear(clear_tile_cache)
    return app
