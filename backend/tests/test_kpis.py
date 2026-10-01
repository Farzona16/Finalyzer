"""U5: the KPI autopilot in a build, the KPI list, the KPI manager edits, roles and isolation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    STRONG_PASSWORD,
    add_user,
    first_admin,
    insert_workspace_with_admin,
    login,
    new_session,
    upload_ready,
    wait_job,
)


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    first_admin(client)
    return client


def build(client: TestClient, dataset_id: str) -> dict:
    response = client.post(f"/api/v1/datasets/{dataset_id}/build")
    assert response.status_code == 202, response.text
    job = wait_job(client, response.json()["id"], timeout=120)
    assert job["status"] == "done", job
    return job


@pytest.fixture
def built(admin: TestClient) -> str:
    dataset_id = upload_ready(admin)
    build(admin, dataset_id)
    return dataset_id


def kpis(client: TestClient, dataset_id: str, lang: str = "en") -> dict:
    response = client.get(f"/api/v1/datasets/{dataset_id}/kpis", params={"lang": lang})
    assert response.status_code == 200, response.text
    return response.json()


def patch(client: TestClient, dataset_id: str, metric_id: str, body: dict, lang: str = "en"):
    return client.patch(
        f"/api/v1/datasets/{dataset_id}/kpis/{metric_id}", json=body, params={"lang": lang}
    )


# ---------------------------------------------------------------- the build runs the autopilot


def test_a_build_runs_the_autopilot_and_stores_the_domain(admin: TestClient, built: str) -> None:
    body = kpis(admin, built)
    assert body["domain"]["pack"] in {"retail", "generic", "sales", "statistics"}
    assert body["items"], "the autopilot accepted no metric"
    assert 1 <= len(body["tiles"]) <= 8
    assert admin.get(f"/api/v1/datasets/{built}").json()["domain"] == body["domain"]["pack"]


def test_every_tile_has_a_real_value_and_a_formatted_text(admin: TestClient, built: str) -> None:
    body = kpis(admin, built)
    tiles = [i for i in body["items"] if i["is_tile"]]
    assert [t["id"] for t in tiles] == body["tiles"]
    for tile in tiles:
        assert tile["value"] is not None and tile["display"] not in ("", "â€”")
        assert tile["label"] and tile["description"]


def test_the_build_job_reports_the_autopilot_step(admin: TestClient, monkeypatch) -> None:
    from app.jobs.runner import JobContext

    seen: list[str] = []
    original = JobContext.progress

    def record(self, percent: int, message_key: str) -> None:
        seen.append(message_key)
        original(self, percent, message_key)

    monkeypatch.setattr(JobContext, "progress", record)
    build(admin, upload_ready(admin))
    assert "jobs.autopilot" in seen
    assert seen.index("jobs.building") < seen.index("jobs.autopilot")


def test_the_demo_retail_dataset_is_recognised_as_retail(admin: TestClient) -> None:
    response = admin.post("/api/v1/demo/retail/load")
    job = wait_job(admin, response.json()["id"], timeout=120)
    dataset_id = job["result"]["dataset_id"]
    build(admin, dataset_id)
    body = kpis(admin, dataset_id, "uz")
    assert body["domain"]["pack"] == "retail"
    assert body["domain"]["level"] == "high"
    assert "retail" not in body["domain"]["headline"].lower() or body["domain"]["label"]
    assert body["domain"]["reasons"]
    tiles = [i for i in body["items"] if i["is_tile"]]
    assert len(tiles) >= 4
    assert any(t["delta_display"] for t in tiles)  # a period comparison exists
    assert body["period"]["start"] < body["period"]["end"]


# ---------------------------------------------------------------- languages


def test_labels_follow_the_requested_language(admin: TestClient) -> None:
    job = wait_job(admin, admin.post("/api/v1/demo/retail/load").json()["id"], timeout=120)
    dataset_id = job["result"]["dataset_id"]
    build(admin, dataset_id)
    uz, en = kpis(admin, dataset_id, "uz"), kpis(admin, dataset_id, "en")
    assert [i["id"] for i in uz["items"]] == [i["id"] for i in en["items"]]
    assert [i["label"] for i in uz["items"]] != [i["label"] for i in en["items"]]
    assert uz["domain"]["headline"].startswith("Biz ma'lumotingizni")
    assert en["domain"]["headline"].startswith("We think")


def test_the_language_defaults_to_the_request_language(admin: TestClient, built: str) -> None:
    default = admin.get(f"/api/v1/datasets/{built}/kpis").json()
    assert default["domain"]["headline"].startswith("Biz ")  # Uzbek is the default
    english = admin.get(f"/api/v1/datasets/{built}/kpis", headers={"Accept-Language": "en"}).json()
    assert english["domain"]["headline"].startswith("We think")


def test_an_unknown_language_is_refused(admin: TestClient, built: str) -> None:
    response = admin.get(f"/api/v1/datasets/{built}/kpis", params={"lang": "xx"})
    assert response.status_code == 422


# ---------------------------------------------------------------- the KPI manager


def test_a_kpi_can_be_hidden_renamed_and_moved(admin: TestClient, built: str) -> None:
    before = kpis(admin, built)
    first, second = before["tiles"][0], before["tiles"][1]

    hidden = patch(admin, built, first, {"enabled": False}).json()
    assert first not in hidden["tiles"]
    assert next(i for i in hidden["items"] if i["id"] == first)["enabled"] is False

    renamed = patch(admin, built, second, {"label": {"en": "My favourite number"}}).json()
    assert next(i for i in renamed["items"] if i["id"] == second)["label"] == "My favourite number"
    other_language = kpis(admin, built, "uz")
    assert next(i for i in other_language["items"] if i["id"] == second)["label"] != (
        "My favourite number"
    )

    moved = patch(admin, built, second, {"order": 99}).json()
    assert moved["tiles"][-1] == second


def test_a_hidden_kpi_can_be_turned_on_again(admin: TestClient, built: str) -> None:
    first = kpis(admin, built)["tiles"][0]
    patch(admin, built, first, {"enabled": False})
    assert first in patch(admin, built, first, {"enabled": True}).json()["tiles"]


def test_the_edits_survive_a_rebuild(admin: TestClient, built: str) -> None:
    first = kpis(admin, built)["tiles"][0]
    patch(admin, built, first, {"label": {"en": "Kept name"}, "enabled": False})
    build(admin, built)
    body = kpis(admin, built)
    item = next(i for i in body["items"] if i["id"] == first)
    assert item["label"] == "Kept name" and first not in body["tiles"]


def test_bad_edits_are_refused(admin: TestClient, built: str) -> None:
    first = kpis(admin, built)["tiles"][0]
    assert patch(admin, built, "no_such_metric", {"enabled": False}).status_code == 404
    assert patch(admin, built, first, {"label": {"en": ""}}).status_code == 422
    assert patch(admin, built, first, {"label": {"en": "x" * 101}}).status_code == 422
    assert patch(admin, built, first, {"label": {"en": "bad\x00name"}}).status_code == 422
    assert patch(admin, built, first, {"label": {"fr": "Salut"}}).status_code == 422
    assert patch(admin, built, first, {"order": -1}).status_code == 422
    assert patch(admin, built, first, {"unknown": 1}).status_code == 422


def test_a_label_is_returned_as_plain_text(admin: TestClient, built: str) -> None:
    first = kpis(admin, built)["tiles"][0]
    body = patch(admin, built, first, {"label": {"en": "<script>alert(1)</script>"}}).json()
    item = next(i for i in body["items"] if i["id"] == first)
    assert item["label"] == "<script>alert(1)</script>"  # stored as text; React escapes it


# ---------------------------------------------------------------- the domain


def test_a_chosen_domain_is_kept_and_used_by_the_next_build(admin: TestClient, built: str) -> None:
    response = admin.patch(f"/api/v1/datasets/{built}/model", json={"domain": "bank"})
    assert response.status_code == 200
    assert kpis(admin, built)["domain"] is None  # the old result is stale until the next build
    build(admin, built)
    body = kpis(admin, built)
    assert body["domain"]["pack"] == "bank" and body["domain"]["source"] == "user"
    assert admin.get(f"/api/v1/datasets/{built}").json()["domain"] == "bank"


# ---------------------------------------------------------------- roles and isolation


def test_a_dataset_that_was_not_built_has_no_kpis(admin: TestClient) -> None:
    dataset_id = upload_ready(admin)
    body = kpis(admin, dataset_id)
    assert body == {"domain": None, "tiles": [], "items": [], "period": None}


def test_a_viewer_reads_but_cannot_edit_and_does_not_see_sql(admin: TestClient, built: str) -> None:
    add_user(admin, "viewer@example.com", "viewer")
    viewer = new_session(admin)
    assert login(viewer, "viewer@example.com") == 200
    body = kpis(viewer, built)
    assert all(item["expert"] is None for item in body["items"])
    first = body["tiles"][0]
    assert patch(viewer, built, first, {"enabled": False}).status_code == 403


def test_analysts_and_admins_see_the_definition_and_sql(admin: TestClient, built: str) -> None:
    add_user(admin, "analyst@example.com", "analyst")
    analyst = new_session(admin)
    assert login(analyst, "analyst@example.com") == 200
    for client in (admin, analyst):
        item = kpis(client, built)["items"][0]
        assert item["expert"]["sql"].upper().startswith("SELECT")
        assert "FROM data" in item["expert"]["sql"]
    first = kpis(analyst, built)["tiles"][0]
    assert patch(analyst, built, first, {"enabled": True}).status_code == 200


def test_another_workspace_gets_not_found(admin: TestClient, built: str) -> None:
    insert_workspace_with_admin(admin, "boss@other.example")
    other = new_session(admin)
    assert login(other, "boss@other.example", STRONG_PASSWORD) == 200
    first = kpis(admin, built)["tiles"][0]
    assert other.get(f"/api/v1/datasets/{built}/kpis").status_code == 404
    assert patch(other, built, first, {"enabled": False}).status_code == 404
    assert first in kpis(admin, built)["tiles"]  # untouched


def test_an_anonymous_visitor_is_refused(admin: TestClient, built: str) -> None:
    anonymous = new_session(admin)
    assert anonymous.get(f"/api/v1/datasets/{built}/kpis").status_code == 401
