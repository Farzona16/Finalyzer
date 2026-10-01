"""Health, readiness and version (public)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import __version__
from app.deps import get_db
from app.models import User
from app.schemas.auth import VersionOut

router = APIRouter(tags=["meta"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process answers."""
    return {"status": "ok"}


@router.get("/readyz")
def readyz(db: Session = Depends(get_db)) -> JSONResponse:
    """Readiness: the database answers."""
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # any database failure means "not ready"; details stay in the logs
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ready"})


@router.get("/version", response_model=VersionOut)
def version(db: Session = Depends(get_db)) -> VersionOut:
    """Build version and whether the first-run screen is needed."""
    has_users = db.scalar(select(User.id).limit(1)) is not None
    return VersionOut(version=__version__, first_run_required=not has_users)


def root_healthz() -> dict[str, str]:
    """Same as `/api/v1/healthz`, at the root for hosting health checks."""
    return {"status": "ok"}
