"""Health, readiness and version (public)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import __version__
from app.deps import get_db
from app.models import User
from app.schemas.auth import VersionOut
from app.services.engine import core_settings, make_gateway

router = APIRouter(tags=["meta"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process answers."""
    return {"status": "ok"}


@router.get("/readyz")
def readyz(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Readiness: the database answers."""
    return ready_payload(request, db)


@router.get("/health/ready")
def health_ready_alias(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Same readiness payload under ``/api/v1`` (the public path is ``/health/ready``)."""
    return ready_payload(request, db)


def ready_payload(request: Request, db: Session) -> JSONResponse:
    """DB, provider chain and kill-switch. Not for pings."""
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # any database failure means "not ready"; details stay in the logs
        return JSONResponse({"status": "unavailable"}, status_code=503)
    services = request.app.state.services
    settings = core_settings(services.settings)
    status = make_gateway(services, None, settings=settings).status()
    return JSONResponse(
        {
            "status": "ready",
            "kill_switch": status["kill_switch"],
            "template_mode": status["template_mode"],
            "providers": [
                {"name": p.get("name"), "state": p.get("state"), "model": p.get("model")}
                for p in status["providers"]
            ],
        }
    )


@router.get("/version", response_model=VersionOut)
def version(db: Session = Depends(get_db)) -> VersionOut:
    """Build version and whether the first-run screen is needed."""
    has_users = db.scalar(select(User.id).limit(1)) is not None
    return VersionOut(version=__version__, first_run_required=not has_users)


def root_healthz() -> dict[str, str]:
    """Same as `/api/v1/healthz`, at the root for hosting health checks."""
    return {"status": "ok"}


def root_ready(request: Request) -> JSONResponse:
    """``GET /health/ready``: DB, providers, kill-switch. Keep-alives must not hit this."""
    with request.app.state.session_factory() as db:
        return ready_payload(request, db)
