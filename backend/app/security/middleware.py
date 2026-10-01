"""Two small ASGI middlewares: request context (id, access log, last-resort 500) and S8 headers."""

from __future__ import annotations

import logging
import re
import time
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import BackendSettings
from app.errors import log_unhandled, problem_response
from app.i18n import parse_accept_language
from app.logging_setup import request_id_var

logger = logging.getLogger("finalyzer.access")
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{8,64}$")

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' data:; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "X-Frame-Options": "DENY",  # old browsers that ignore frame-ancestors
}
HSTS = "max-age=31536000; includeSubDomains"


class RequestContextMiddleware:
    """Gives every request an id, logs one access line and turns crashes into a clean 500."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        incoming = headers.get("x-request-id", "")
        request_id = incoming if REQUEST_ID_PATTERN.match(incoming) else uuid.uuid4().hex[:16]
        token = request_id_var.set(request_id)
        state = {"status": 500, "started": False}
        started_at = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                state["status"], state["started"] = message["status"], True
                MutableHeaders(scope=message).append("X-Request-ID", request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception as exc:  # last resort: no stack trace may reach the client (S19)
            log_unhandled(exc, scope["method"], scope["path"])
            if not state["started"]:
                locale = parse_accept_language(headers.get("accept-language"))
                response = problem_response("server_error", locale=locale, instance=scope["path"])
                await response(scope, receive, send_with_id)
        finally:
            elapsed_ms = round((time.perf_counter() - started_at) * 1000)
            logger.info(
                "request method=%s path=%s status=%s ms=%s",
                scope["method"],
                scope["path"],
                state["status"],
                elapsed_ms,
            )
            request_id_var.reset(token)


class SecurityHeadersMiddleware:
    """Adds the security headers to every response (S8)."""

    def __init__(self, app: ASGIApp, settings: BackendSettings) -> None:
        self.app = app
        self.trust_proxy = settings.trust_proxy_headers

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        secure = scope.get("scheme") == "https" or (
            self.trust_proxy and headers.get("x-forwarded-proto", "").lower() == "https"
        )
        is_api = scope["path"].startswith("/api/")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                out = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    out[name] = value
                if secure:
                    out["Strict-Transport-Security"] = HSTS
                if is_api:
                    out.setdefault("Cache-Control", "no-store")
            await send(message)

        await self.app(scope, receive, send_with_headers)
