"""ASGI middleware that answers ``GET /health`` without running FastAPI handlers.

Keep-alive pings hit only this path (spec 2.3). The module uses the standard library only.
"""

from __future__ import annotations

BODY = b'{"status":"ok"}'
PATHS = ("/health", "/healthz")


class HealthShortcut:
    """Outermost ASGI wrapper: 200 JSON for ``/health`` and ``/healthz``, else pass through."""

    def __init__(self, app) -> None:  # noqa: ANN001
        self.app = app

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if (
            scope.get("type") == "http"
            and scope.get("method") == "GET"
            and scope.get("path") in PATHS
        ):
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(BODY)).encode()),
                        (b"cache-control", b"no-store"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": BODY})
            return
        await self.app(scope, receive, send)


def health_app(app):  # noqa: ANN001, ANN201
    """Wrap an ASGI app (used in tests of this module)."""
    return HealthShortcut(app)
