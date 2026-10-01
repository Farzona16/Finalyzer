"""Job state and Server-Sent Events (`/api/v1/jobs/...`)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.deps import get_db, require
from app.errors import AppError, locale_of
from app.i18n import translate
from app.models import Job, User
from app.schemas.datasets import JobError, JobOut
from app.security.permissions import Permission

router = APIRouter(prefix="/jobs", tags=["jobs"])
POLL_SECONDS = 0.25
STREAM_LIMIT_SECONDS = 15 * 60
FINAL_STATES = ("done", "failed")


def job_out(job: Job, locale: str) -> JobOut:
    """The public view of a job; a failure carries localized title and detail."""
    payload = json.loads(job.result) if job.result else None
    error = None
    result = None
    if payload and payload.get("ok") is False:
        info = payload.get("error", {})
        code = str(info.get("code", "server_error"))
        error = JobError(
            code=code,
            title=translate(locale, f"errors.{code}.title"),
            detail=translate(locale, f"errors.{code}.detail"),
            reason=info.get("reason") or None,
        )
    elif payload:
        result = {key: value for key, value in payload.items() if key != "ok"}
    return JobOut(
        id=job.id,
        kind=job.kind,
        status=job.status,  # type: ignore[arg-type]
        progress=job.progress,
        message_key=job.message_key,
        result=result,
        error=error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def find_job(db: Session, user: User, job_id: str) -> Job:
    """A job of the user's workspace, or "not found"."""
    job = db.get(Job, job_id)
    if job is None or job.workspace_id != user.workspace_id:
        raise AppError("not_found")
    return job


@router.get("/{job_id}", response_model=JobOut)
def get_job(
    job_id: str,
    request: Request,
    user: User = Depends(require(Permission.READ)),
    db: Session = Depends(get_db),
) -> JobOut:
    """State of one job."""
    return job_out(find_job(db, user, job_id), locale_of(request))


def _snapshot(request: Request, workspace_id: str, job_id: str) -> Job | None:
    """Read the job with a short-lived session (the request session is not used in the loop)."""
    with request.app.state.session_factory() as db:
        job = db.get(Job, job_id)
        if job is None or job.workspace_id != workspace_id:
            return None
        db.expunge(job)
        return job


@router.get("/{job_id}/events")
async def job_events(
    job_id: str,
    request: Request,
    user: User = Depends(require(Permission.READ)),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Progress as Server-Sent Events: ``progress`` events, then one ``done`` or ``failed``."""
    first = find_job(db, user, job_id)
    locale, workspace_id = locale_of(request), user.workspace_id
    db.expunge(first)

    async def stream() -> AsyncIterator[str]:
        last: tuple[str, int, str] | None = None
        waited = 0.0
        while waited < STREAM_LIMIT_SECONDS:
            job = await run_in_threadpool(_snapshot, request, workspace_id, job_id)
            if job is None:
                return
            state = (job.status, job.progress, job.message_key)
            if state != last or job.status in FINAL_STATES:
                last = state
                event = job.status if job.status in FINAL_STATES else "progress"
                body = job_out(job, locale).model_dump_json()
                yield f"event: {event}\ndata: {body}\n\n"
            if job.status in FINAL_STATES:
                return
            await asyncio.sleep(POLL_SECONDS)
            waited += POLL_SECONDS

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
