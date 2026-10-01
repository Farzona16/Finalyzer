"""Serves the built React app from the same origin (decision D4)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, Response
from starlette.exceptions import HTTPException

IMMUTABLE = "public, max-age=31536000, immutable"
PLACEHOLDER = (
    '<!doctype html><html lang="uz"><head><meta charset="utf-8">'
    "<title>Finalyzer</title></head><body>"
    "<h1>Finalyzer</h1>"
    "<p>API ishlayapti. Foydalanuvchi interfeysi hali yigʻilmagan "
    "(<code>npm run build</code>).</p>"
    "<p>The API is running. The user interface has not been built yet.</p>"
    "</body></html>"
)


def install_spa(app: FastAPI, dist: Path) -> None:
    """Serve files from ``dist`` and fall back to ``index.html`` for client-side routes."""
    root = dist.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve(full_path: str) -> Response:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        target = (root / full_path).resolve()
        if full_path and target.is_file() and target.is_relative_to(root):
            headers = {"Cache-Control": IMMUTABLE} if full_path.startswith("assets/") else {}
            return FileResponse(target, headers=headers)
        index = root / "index.html"
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return HTMLResponse(PLACEHOLDER)
