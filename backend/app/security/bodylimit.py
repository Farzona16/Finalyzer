"""Request-size guard for uploads (S9): refuse a body before it is spooled to disk.

Starlette's multipart parser has no total limit. This middleware rejects a request whose
``Content-Length`` is above the limit, and counts bytes for chunked bodies without one.
"""

from __future__ import annotations

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.errors import problem_response
from app.i18n import parse_accept_language


class _TooLarge(Exception):
    """Raised inside ``receive`` when a chunked body grows past the limit."""


class BodyLimitMiddleware:
    """Limits the body size of POST requests to the listed path prefixes."""

    def __init__(self, app: ASGIApp, *, prefixes: tuple[str, ...], limit_bytes: int) -> None:
        self.app = app
        self.prefixes = prefixes
        self.limit = limit_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or not scope["path"].startswith(self.prefixes)
        ):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        declared = headers.get("content-length", "")
        if declared.isdigit() and int(declared) > self.limit:
            await self._reject(scope, receive, send, headers)
            return
        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    raise _TooLarge
            return message

        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _TooLarge:
            if not started:
                await self._reject(scope, receive, send, headers)

    async def _reject(self, scope: Scope, receive: Receive, send: Send, headers: Headers) -> None:
        locale = parse_accept_language(headers.get("accept-language"))
        response = problem_response("payload_too_large", locale=locale, instance=scope["path"])
        await response(scope, receive, send)
