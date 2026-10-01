"""Chaos: a down or noisy LLM, a busy store, and a large result still end in a readable state."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import TestClient as RawClient

from tests.conftest import first_admin, wait_job


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    first_admin(client)
    return client


def _ready(admin: TestClient) -> str:
    job = wait_job(admin, admin.post("/api/v1/demo/retail/load").json()["id"], timeout=120)
    dataset_id = job["result"]["dataset_id"]
    job_id = admin.post(f"/api/v1/datasets/{dataset_id}/build").json()["id"]
    built = wait_job(admin, job_id, timeout=120)
    assert built["status"] == "done", built
    return dataset_id


def test_ask_returns_a_plain_error_when_the_engine_raises(
    admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_id = _ready(admin)
    from app.services import ask as ask_service

    def boom(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("provider down")

    monkeypatch.setattr(ask_service, "answer", boom)
    crashing = RawClient(admin.app, raise_server_exceptions=False, base_url="http://testserver")
    crashing.cookies.update(admin.cookies)
    crashing.headers.update(admin.headers)
    response = crashing.post(
        f"/api/v1/datasets/{dataset_id}/ask",
        json={"question": "Jami daromad qancha?", "lang": "uz"},
    )
    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "server_error"
    assert "provider down" not in response.text
    assert "Traceback" not in response.text
    assert body.get("correlation_id")


def test_a_large_result_is_capped_not_an_exception(admin: TestClient) -> None:
    dataset_id = _ready(admin)
    response = admin.post(
        f"/api/v1/datasets/{dataset_id}/ask",
        json={"question": "Har bir mahsulot bo'yicha savdo qancha?", "lang": "uz"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] in {"ok", "clarify", "refuse", "failed"}
    if body.get("table"):
        assert body["table"]["row_count"] <= 10_000


def test_an_unknown_job_is_not_found_not_a_stack_trace(admin: TestClient) -> None:
    response = admin.get("/api/v1/jobs/" + "0" * 32)
    assert response.status_code == 404
    assert "Traceback" not in response.text
