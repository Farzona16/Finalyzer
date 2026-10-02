"""Ask v2 API: a routed question on the retail demo, SSE stages, isolation."""

from __future__ import annotations

import pytest
from app.services.ask import without_sql
from fastapi.testclient import TestClient

from core.models import QueryResult
from core.nlu.plain_answer import PlainAnswer
from tests.conftest import (
    STRONG_PASSWORD,
    add_user,
    first_admin,
    insert_workspace_with_admin,
    login,
    new_session,
    wait_job,
)


def test_without_sql_keeps_the_reasoning() -> None:
    answer = PlainAnswer(
        status="ok",
        headline="h",
        explanation="Hisoblangan qiymat: 12.",
        details="Savol «total» deb tushunildi.",
        sql="SELECT SUM(amount) FROM data",
        table=QueryResult(
            sql="SELECT SUM(amount) FROM data",
            columns=["revenue"],
            rows=[[12]],
            row_count=1,
        ),
    )
    redacted = without_sql(answer)
    assert redacted.sql is None
    assert redacted.table is not None and redacted.table.sql == ""
    assert redacted.table.rows == [[12]]
    assert "total" in redacted.details
    assert "SELECT" not in redacted.details


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    first_admin(client)
    return client


def _built_demo(admin: TestClient, name: str = "retail") -> str:
    job = wait_job(admin, admin.post(f"/api/v1/demo/{name}/load").json()["id"], timeout=120)
    assert job["status"] == "done", job
    dataset_id = job["result"]["dataset_id"]
    build = admin.post(f"/api/v1/datasets/{dataset_id}/build").json()["id"]
    built = wait_job(admin, build, timeout=120)
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
    assert body["table"]["rows"]
    assert "Hisoblangan qiymat" in body["explanation"]
    assert "qator" not in body["headline"]
    assert body["sql"] and "SELECT" in body["sql"].upper()
    assert body["details"]
    assert "tuzildi" in body["details"] or "total" in body["details"]


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
    assert "SELECT" in text.upper()
    assert "Hisoblangan qiymat" in text


def test_suggested_questions_come_from_the_pack(admin: TestClient) -> None:
    dataset_id = _built_demo(admin)
    path = f"/api/v1/datasets/{dataset_id}/suggested-questions"
    body = admin.get(path, params={"lang": "uz"}).json()
    assert len(body["questions"]) >= 3
    assert any("daromad" in q.lower() for q in body["questions"])


def test_another_workspace_cannot_ask(admin: TestClient) -> None:
    dataset_id = _built_demo(admin)
    insert_workspace_with_admin(admin, "boss@other.example")
    other = new_session(admin)
    assert login(other, "boss@other.example", STRONG_PASSWORD) == 200
    asked = other.post(
        f"/api/v1/datasets/{dataset_id}/ask",
        json={"question": "Jami daromad qancha?"},
    )
    assert asked.status_code == 404
    assert other.get(f"/api/v1/datasets/{dataset_id}/suggested-questions").status_code == 404


def test_a_viewer_gets_the_numbers_but_not_the_sql(admin: TestClient) -> None:
    dataset_id = _built_demo(admin)
    add_user(admin, "look@example.com", "viewer")
    viewer = new_session(admin)
    assert login(viewer, "look@example.com") == 200
    body = viewer.post(
        f"/api/v1/datasets/{dataset_id}/ask",
        json={"question": "Jami daromad qancha?", "lang": "uz"},
    ).json()
    assert body["status"] == "ok"
    assert "Hisoblangan qiymat" in body["explanation"]
    assert body["table"]["rows"]
    assert body["sql"] is None
    assert body["table"]["sql"] == ""
    assert body["details"]
    assert "SELECT" not in body["details"].upper()
