"""Ask v2 API: a routed question on the retail demo, SSE stages, isolation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import STRONG_PASSWORD, first_admin, insert_workspace_with_admin, login, new_session, wait_job


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    first_admin(client)
    return client


def _built_demo(admin: TestClient, name: str = "retail") -> str:
    job = wait_job(admin, admin.post(f"/api/v1/demo/{name}/load").json()["id"], timeout=120)
    assert job["status"] == "done", job
    dataset_id = job["result"]["dataset_id"]
    built = wait_job(admin, admin.post(f"/api/v1/datasets/{dataset_id}/build").json()["id"], timeout=120)
    assert built["status"] == "done", built
    return dataset_id


def test_a_plain_uzbek_question_returns_a_verified_number(admin: TestClient) -> None:
    dataset_id = _built_demo(admin)
    response = admin.post(
        f"/api/v1/datasets/{dataset_id}/ask",
        json={"question": "Jami daromad qancha?", "lang": "uz"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok" and body["verified"]
    assert body["table"]["row_count"] == 1


def test_ask_stream_ends_with_done(admin: TestClient) -> None:
    dataset_id = _built_demo(admin)
    with admin.stream(
        "GET",
        f"/api/v1/datasets/{dataset_id}/ask/stream",
        params={"question": "Jami daromad qancha?", "lang": "uz"},
    ) as response:
        text = b"".join(response.iter_bytes()).decode("utf-8")
    assert "event: understanding" in text
    assert "event: done" in text


def test_suggested_questions_come_from_the_pack(admin: TestClient) -> None:
    dataset_id = _built_demo(admin)
    body = admin.get(f"/api/v1/datasets/{dataset_id}/suggested-questions", params={"lang": "uz"}).json()
    assert len(body["questions"]) >= 3
    assert any("daromad" in q.lower() for q in body["questions"])


def test_another_workspace_cannot_ask(admin: TestClient) -> None:
    dataset_id = _built_demo(admin)
    insert_workspace_with_admin(admin, "boss@other.example")
    other = new_session(admin)
    assert login(other, "boss@other.example", STRONG_PASSWORD) == 200
    assert other.post(f"/api/v1/datasets/{dataset_id}/ask", json={"question": "Jami daromad qancha?"}).status_code == 404
    assert other.get(f"/api/v1/datasets/{dataset_id}/suggested-questions").status_code == 404
