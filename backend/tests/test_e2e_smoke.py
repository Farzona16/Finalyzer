"""U13 smoke: first admin → demo → dashboard → insights → Ask, all offline."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import first_admin, wait_job


def test_first_admin_demo_dashboard_insights_and_ask(client: TestClient) -> None:
    first_admin(client)
    loaded = wait_job(client, client.post("/api/v1/demo/retail/load").json()["id"], timeout=120)
    assert loaded["status"] == "done", loaded
    dataset_id = loaded["result"]["dataset_id"]
    job_id = client.post(f"/api/v1/datasets/{dataset_id}/build").json()["id"]
    built = wait_job(client, job_id, timeout=120)
    assert built["status"] == "done", built

    dash = client.get(f"/api/v1/datasets/{dataset_id}/dashboard", params={"lang": "uz"}).json()
    assert len(dash["tiles"]) >= 8

    insights = client.get(
        f"/api/v1/datasets/{dataset_id}/insights", params={"lang": "uz", "level": "simple"}
    ).json()
    assert insights["cards"]

    asked = client.post(
        f"/api/v1/datasets/{dataset_id}/ask",
        json={"question": "Jami daromad qancha?", "lang": "uz"},
    )
    assert asked.status_code == 200, asked.text
    body = asked.json()
    assert body["status"] == "ok" and body["verified"]

    privacy = client.get("/api/v1/privacy/summary").json()
    assert privacy["bytes_sent"] == 0
    assert privacy["mode"] == "offline"
