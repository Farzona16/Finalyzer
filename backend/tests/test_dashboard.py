"""Dashboard template and tile queries on a built retail demo."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import first_admin, wait_job


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    first_admin(client)
    return client


def _ready(admin: TestClient, name: str = "retail") -> str:
    job = wait_job(admin, admin.post(f"/api/v1/demo/{name}/load").json()["id"], timeout=120)
    dataset_id = job["result"]["dataset_id"]
    job_id = admin.post(f"/api/v1/datasets/{dataset_id}/build").json()["id"]
    built = wait_job(admin, job_id, timeout=120)
    assert built["status"] == "done", built
    return dataset_id


@pytest.mark.parametrize("name", ["retail", "bank", "sales", "statistics"])
def test_each_core_demo_dashboard_has_at_least_eight_tiles(admin: TestClient, name: str) -> None:
    dataset_id = _ready(admin, name)
    body = admin.get(f"/api/v1/datasets/{dataset_id}/dashboard", params={"lang": "uz"}).json()
    assert len(body["tiles"]) >= 8, (name, [t["id"] for t in body["tiles"]])
    assert all(t["title"] for t in body["tiles"])


def test_a_tile_query_returns_formatted_numbers(admin: TestClient) -> None:
    dataset_id = _ready(admin)
    dash = admin.get(f"/api/v1/datasets/{dataset_id}/dashboard").json()
    tile = next(t for t in dash["tiles"] if t["type"] in {"donut", "bar_h", "line"})
    response = admin.post(
        f"/api/v1/datasets/{dataset_id}/tiles/query",
        json={"tile_id": tile["id"], "filters": {}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["series"] or body["rows"]
    assert "source_sql" in body


def test_cross_filter_changes_the_numbers(admin: TestClient) -> None:
    dataset_id = _ready(admin)
    dash = admin.get(f"/api/v1/datasets/{dataset_id}/dashboard").json()
    slicer = dash["slicers"][0]
    value = slicer["values"][0]
    tile = next(t for t in dash["tiles"] if t["type"] == "line")
    url = f"/api/v1/datasets/{dataset_id}/tiles/query"
    full = admin.post(url, json={"tile_id": tile["id"], "filters": {}}).json()
    filtered = admin.post(
        url,
        json={
            "tile_id": tile["id"],
            "filters": {"extra_column": slicer["column"], "extra_value": value},
        },
    ).json()
    assert full["series"] and filtered["series"]
    assert full["series"][0]["values"] != filtered["series"][0]["values"]
