"""Problem-details errors (RFC 9457 style) with Uzbek/English text and a correlation id (S19).

Clients never see stack traces or exception messages. Every error body carries the request id
as ``correlation_id`` so a user can quote it and an operator can find the log line.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.i18n import parse_accept_language, translate
from app.logging_setup import request_id_var

logger = logging.getLogger("finalyzer.errors")

ERROR_STATUS: dict[str, int] = {
    "bad_request": 400,
    "not_authenticated": 401,
    "invalid_credentials": 401,
    "invalid_token": 401,
    "account_locked": 429,
    "rate_limited": 429,
    "forbidden": 403,
    "csrf_failed": 403,
    "origin_not_allowed": 403,
    "not_found": 404,
    "method_not_allowed": 405,
    "validation_failed": 422,
    "payload_too_large": 413,
    "unsupported_media": 415,
    "upload_invalid": 422,
    "upload_unsafe": 422,
    "dataset_busy": 409,
    "model_invalid": 422,
    "first_admin_exists": 409,
    "email_taken": 409,
    "last_admin": 409,
    "cannot_modify_self": 409,
    "password_too_short": 422,
    "password_too_long": 422,
    "password_too_common": 422,
    "password_equals_email": 422,
    "privacy_mode_not_allowed": 422,
    "server_error": 500,
    "service_unavailable": 503,
}

STATUS_TO_CODE = {
    400: "bad_request",
    401: "not_authenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    413: "payload_too_large",
    415: "unsupported_media",
    422: "validation_failed",
    429: "rate_limited",
    503: "service_unavailable",
}


class AppError(Exception):
    """An expected failure with a stable code. The code selects status and texts."""

    def __init__(
        self,
        code: str,
        *,
        params: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
        extra: dict[str, object] | None = None,
    ) -> None:
        super().__init__(code)
        if code not in ERROR_STATUS:
            raise ValueError(f"unknown error code: {code}")
        self.code = code
        self.status = ERROR_STATUS[code]
        self.params = params or {}
        self.headers = headers or {}
        self.extra = extra or {}


def locale_of(request: Request) -> str:
    """The reply language: ``Accept-Language`` or Uzbek."""
    return parse_accept_language(request.headers.get("accept-language"))


def problem_response(
    code: str,
    *,
    locale: str,
    instance: str,
    params: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
    extra: dict[str, object] | None = None,
) -> JSONResponse:
    """Build the JSON problem document for an error code."""
    correlation_id = request_id_var.get()
    values = {"correlation_id": correlation_id, **(params or {})}
    body: dict[str, object] = {
        "type": f"urn:finalyzer:problem:{code}",
        "code": code,
        "title": translate(locale, f"errors.{code}.title", **values),
        "status": ERROR_STATUS[code],
        "detail": translate(locale, f"errors.{code}.detail", **values),
        "instance": instance,
        "correlation_id": correlation_id,
        **(extra or {}),
    }
    return JSONResponse(
        body,
        status_code=ERROR_STATUS[code],
        headers=headers,
        media_type="application/problem+json",
    )


async def handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    """Render an ``AppError``."""
    assert isinstance(exc, AppError)
    return problem_response(
        exc.code,
        locale=locale_of(request),
        instance=request.url.path,
        params=exc.params,
        headers=exc.headers,
        extra=exc.extra,
    )


async def handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    """Render Starlette/FastAPI errors (404 for unknown paths, 405, ...) as problem details."""
    assert isinstance(exc, StarletteHTTPException)
    code = STATUS_TO_CODE.get(exc.status_code)
    if code is None:
        code = "server_error" if exc.status_code >= 500 else "bad_request"
    headers = dict(exc.headers) if exc.headers else None
    return problem_response(
        code, locale=locale_of(request), instance=request.url.path, headers=headers
    )


async def handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    """Render a 422 listing fields and error types, never the submitted values."""
    assert isinstance(exc, RequestValidationError)
    fields = [
        {"loc": [str(part) for part in err["loc"]], "type": str(err["type"])}
        for err in exc.errors()
    ]
    return problem_response(
        "validation_failed",
        locale=locale_of(request),
        instance=request.url.path,
        extra={"errors": fields},
    )


def log_unhandled(exc: BaseException, method: str, path: str) -> None:
    """Log an unexpected exception as type and frames only (see ``logging_setup``)."""
    logger.error(
        "unhandled_exception method=%s path=%s type=%s",
        method,
        path,
        type(exc).__name__,
        exc_info=(type(exc), exc, exc.__traceback__),
    )


def install_error_handlers(app: FastAPI) -> None:
    """Register the handlers. Unhandled exceptions are handled in the request middleware."""
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
