"""The checked-in OpenAPI snapshot matches the running app (decision D22)."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
OPENAPI = ROOT / "frontend" / "src" / "api" / "openapi.json"
SCHEMA = ROOT / "frontend" / "src" / "api" / "schema.ts"


def test_openapi_snapshot_lists_every_path(client: TestClient) -> None:
    live = client.app.openapi()
    stored = json.loads(OPENAPI.read_text(encoding="utf-8"))
    assert set(stored["paths"]) == set(live["paths"])
    text = SCHEMA.read_text(encoding="utf-8")
    for path in live["paths"]:
        assert path in text
