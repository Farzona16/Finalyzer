"""Insights cards and the privacy counters."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

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


def test_insights_return_plain_cards(admin: TestClient) -> None:
    dataset_id = _ready(admin)
    body = admin.get(
        f"/api/v1/datasets/{dataset_id}/insights", params={"lang": "uz", "level": "simple"}
    ).json()
    assert body["cards"]
    first = body["cards"][0]
    assert first["headline"] and first["next_step"]
    patched = admin.patch(
        f"/api/v1/datasets/{dataset_id}/insights/{first['finding_id']}",
        json={"status": "done"},
    )
    assert patched.status_code == 200
    again = admin.get(f"/api/v1/datasets/{dataset_id}/insights").json()
    match = next(c for c in again["cards"] if c["finding_id"] == first["finding_id"])
    assert match["status"] == "done"


def test_offline_privacy_counters_are_zero(admin: TestClient) -> None:
    summary = admin.get("/api/v1/privacy/summary").json()
    assert summary["bytes_sent"] == 0
    assert summary["mode"] == "offline"
    assert admin.get("/api/v1/privacy/events").json()["events"] == []
