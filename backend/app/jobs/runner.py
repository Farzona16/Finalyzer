"""In-process job runner (decision D19): a small thread pool plus the ``jobs`` table.

A job function receives a :class:`JobContext` to report progress. It returns a result
dictionary, or raises :class:`JobFailure` with a stable error code. Any other exception becomes
``server_error``; only its type is logged, because messages can quote user data (S16).
"""

from __future__ import annotations

import contextvars
import json
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import Session, sessionmaker

from app import clock
from app.db import session_scope
from app.models import Job
from core.errors import UserFacingError

logger = logging.getLogger("finalyzer.jobs")


class JobFailure(Exception):
    """A job that cannot continue. ``code`` selects the localized text; ``reason`` is English."""

    def __init__(self, code: str, reason: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.reason = reason


class JobContext:
    """What a running job can do: report progress."""

    def __init__(self, runner: JobRunner, job_id: str) -> None:
        self._runner = runner
        self.job_id = job_id

    def progress(self, percent: int, message_key: str) -> None:
        """Store progress (0-100) and the key of a short status text."""
        self._runner._update(
            self.job_id, progress=max(0, min(99, percent)), message_key=message_key
        )


JobFunction = Callable[[JobContext], dict[str, object]]


class JobRunner:
    """Runs jobs on a thread pool and keeps their state in the database."""

    def __init__(self, factory: sessionmaker[Session], workers: int = 2) -> None:
        self._factory = factory
        self._pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="job")
        self._functions: dict[str, JobFunction] = {}
        self._inflight: dict[str, str] = {}

    def submit(
        self,
        workspace_id: str,
        user_id: str,
        kind: str,
        function: JobFunction,
        *,
        flight_key: str | None = None,
    ) -> Job:
        """Record a queued job and start it. Identical ``flight_key`` values share one job."""
        if flight_key:
            existing_id = self._inflight.get(flight_key)
            if existing_id:
                with session_scope(self._factory) as db:
                    job = db.get(Job, existing_id)
                    if job is not None and job.status in ("queued", "running"):
                        db.expunge(job)
                        return job
        with session_scope(self._factory) as db:
            job = Job(
                workspace_id=workspace_id, user_id=user_id, kind=kind, message_key="jobs.queued"
            )
            db.add(job)
            db.flush()
            db.expunge(job)
        self._functions[job.id] = function
        if flight_key:
            self._inflight[flight_key] = job.id
        context = contextvars.copy_context()  # keeps the request id in the job's log lines
        self._pool.submit(context.run, self._run, job.id, function)
        return job

    def restart(self, job_id: str) -> Job | None:
        """Re-run an interrupted job whose function is still in this process."""
        function = self._functions.get(job_id)
        if function is None:
            return None
        with session_scope(self._factory) as db:
            job = db.get(Job, job_id)
            if job is None or job.status != "interrupted":
                return None
            workspace_id, user_id, kind = job.workspace_id, job.user_id, job.kind
        return self.submit(workspace_id, user_id, kind, function)

    def shutdown(self) -> None:
        """Wait for running jobs (app shutdown)."""
        self._pool.shutdown(wait=True, cancel_futures=True)

    # ------------------------------------------------------------------ internals

    def _update(self, job_id: str, **values: object) -> None:
        with session_scope(self._factory) as db:
            job = db.get(Job, job_id)
            if job is not None:
                for key, value in values.items():
                    setattr(job, key, value)
                job.updated_at = clock.now()

    def _run(self, job_id: str, function: JobFunction) -> None:
        self._update(job_id, status="running", progress=1, message_key="jobs.running")
        try:
            result = function(JobContext(self, job_id))
        except JobFailure as failure:
            self._fail(job_id, failure.code, failure.reason)
        except UserFacingError as error:
            self._fail(job_id, "upload_invalid", error.message)
        except Exception as exc:  # noqa: BLE001 - any crash must end the job cleanly
            logger.error("job_crashed job=%s kind_error=%s", job_id, type(exc).__name__)
            self._fail(job_id, "server_error", "")
        else:
            self._update(
                job_id,
                status="done",
                progress=100,
                message_key="jobs.done",
                result=json.dumps({"ok": True, **result}),
            )
        self._inflight = {key: value for key, value in self._inflight.items() if value != job_id}

    def _fail(self, job_id: str, code: str, reason: str) -> None:
        payload = {"ok": False, "error": {"code": code, "reason": reason[:300]}}
        self._update(job_id, status="failed", message_key="jobs.failed", result=json.dumps(payload))
