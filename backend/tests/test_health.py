"""U1: health endpoints, OpenAPI document and the placeholder page."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import first_admin


def test_healthz_works_on_both_paths(client: TestClient) -> None:
    for path in ("/api/v1/healthz", "/healthz"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_readyz_checks_the_database(client: TestClient) -> None:
    body = client.get("/api/v1/readyz").json()
    assert body["status"] == "ready"


def test_readyz_reports_unavailable_when_the_database_is_gone(client: TestClient) -> None:
    client.app.state.session_factory = _broken_factory  # type: ignore[attr-defined]
    response = client.get("/api/v1/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def _broken_factory():  # noqa: ANN202 - test helper standing in for a sessionmaker
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    return Session(bind=create_engine("sqlite:////nonexistent-folder/none.db"))


def test_version_tells_whether_first_run_is_needed(client: TestClient) -> None:
    body = client.get("/api/v1/version").json()
    assert body == {"version": "3.0.0", "first_run_required": True}
    first_admin(client)
    assert client.get("/api/v1/version").json()["first_run_required"] is False


def test_openapi_is_generated_and_has_no_docs_pages(client: TestClient) -> None:
    schema = client.get("/api/v1/openapi.json").json()
    assert schema["info"]["title"] == "Finalyzer"
    assert "/api/v1/auth/login" in schema["paths"]
    assert client.get("/docs").status_code == 200  # falls through to the SPA placeholder
    assert "swagger" not in client.get("/docs").text.lower()


def test_production_does_not_publish_the_openapi_document(make_client) -> None:
    prod = make_client(app_env="production", cookie_secure=True)
    assert prod.get("/api/v1/openapi.json").status_code == 404
