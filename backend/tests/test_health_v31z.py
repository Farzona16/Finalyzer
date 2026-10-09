"""``/health`` is cheap; ``/health/ready`` reports DB and providers (phase V1b)."""

from __future__ import annotations

import ast
from pathlib import Path

from fastapi.testclient import TestClient


def test_health_module_imports_only_the_stdlib() -> None:
    source = Path(__file__).resolve().parents[2] / "core" / "runtime" / "health.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in {
                    "duckdb", "anthropic", "pandas", "fastapi", "sqlalchemy",
                }
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in {
                "duckdb", "anthropic", "pandas", "fastapi", "sqlalchemy", "core",
            }


def test_root_health_and_ready(client: TestClient) -> None:
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    ready = client.get("/health/ready")
    assert ready.status_code == 200
    body = ready.json()
    assert body["status"] == "ready"
    assert "kill_switch" in body
    assert "template_mode" in body
