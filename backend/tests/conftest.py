"""Shared fixtures: an isolated app with its own SQLite file for every test."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from app.config import BackendSettings
from app.db import session_scope
from app.models import User, Workspace
from app.security.passwords import PasswordService, fast_hasher
from fastapi.testclient import TestClient

from app.main import create_app

SECRET = "test-secret-key-0123456789-abcdefghijklmnopqrstuvwxyz"
STRONG_PASSWORD = "Quiet-River-Stone-42"
ADMIN_EMAIL = "admin@example.com"

SettingsFactory = Callable[..., BackendSettings]


@pytest.fixture
def make_settings(tmp_path: Path) -> SettingsFactory:
    """Build test settings (no .env file) with optional overrides."""

    def build(**overrides: object) -> BackendSettings:
        values: dict[str, object] = {
            "app_env": "test",
            "app_secret_key": SECRET,
            "database_url": f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
            "frontend_dist": tmp_path / "dist",
            "data_dir": tmp_path / "data",
            "rate_limit_login_per_min": 1000,
            "rate_limit_api_per_min": 100000,
            "rate_limit_upload_per_hour": 100000,
            "purge_interval_seconds": 0,  # tests call the sweep themselves
        }
        values.update(overrides)
        return BackendSettings(_env_file=None, **values)  # type: ignore[arg-type]

    return build


@pytest.fixture
def make_client(make_settings: SettingsFactory) -> Iterator[Callable[..., TestClient]]:
    """Create clients (each with its own app); lifespans are closed at the end of the test."""
    opened: list[TestClient] = []

    def build(**overrides: object) -> TestClient:
        client = TestClient(create_app(make_settings(**overrides)), base_url="http://testserver")
        client.__enter__()
        opened.append(client)
        return client

    yield build
    for client in opened:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    """A client with default settings."""
    return make_client()


def with_csrf(client: TestClient) -> TestClient:
    """Fetch a CSRF token and send it with every later request of this client."""
    token = client.get("/api/v1/auth/csrf").json()["csrf_token"]
    client.headers["X-CSRF-Token"] = token
    return client


def first_admin(client: TestClient, email: str = ADMIN_EMAIL) -> dict:
    """Run the first-run flow; the client ends up signed in as the administrator."""
    with_csrf(client)
    response = client.post(
        "/api/v1/auth/first-admin",
        json={
            "email": email,
            "password": STRONG_PASSWORD,
            "display_name": "Admin Person",
            "workspace_name": "Acme",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def login(client: TestClient, email: str, password: str = STRONG_PASSWORD) -> int:
    """Sign in; returns the status code."""
    with_csrf(client)
    return client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    ).status_code


def add_user(
    client: TestClient, email: str, role: str = "viewer", password: str = STRONG_PASSWORD
) -> dict:
    """Create a user through the admin API (the client must be signed in as an admin)."""
    response = client.post(
        "/api/v1/admin/users",
        json={
            "email": email,
            "display_name": email.split("@")[0],
            "role": role,
            "password": password,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def new_session(client: TestClient) -> TestClient:
    """A second browser for the same app (separate cookie jar)."""
    return TestClient(client.app, base_url="http://testserver")  # no lifespan: app is running


def insert_workspace_with_admin(client: TestClient, email: str) -> str:
    """Create a second workspace and its admin directly in the database; returns the user id."""
    app = client.app
    passwords = PasswordService(fast_hasher())
    with session_scope(app.state.session_factory) as db:
        workspace = Workspace(name="Other company")
        user = User(
            workspace=workspace,
            email=email,
            display_name="Other Admin",
            role="admin",
            password_hash=passwords.hash(STRONG_PASSWORD),
        )
        db.add_all([workspace, user])
        db.flush()
        return user.id


# ---------------------------------------------------------------- datasets and jobs


def sales_csv(rows: int = 120, canary: str = "") -> bytes:
    """A small sales table with a date, a category, a numeric amount and a personal e-mail."""
    lines = ["order_date,store,customer_id,amount,email,note"]
    for i in range(rows):
        day = 1 + i % 28
        lines.append(
            f"2026-03-{day:02d},Store {i % 3},C{i % 10:03d},{100 + (i * 7) % 50}.5,"
            f"person{i}@example.com,{canary or 'ok'}"
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


def customers_csv() -> bytes:
    """A dimension table whose key matches ``sales_csv().customer_id``."""
    lines = ["customer_id,region"] + [f"C{i:03d},Region {i % 2}" for i in range(10)]
    return ("\n".join(lines) + "\n").encode("utf-8")


def upload(client: TestClient, files: list[tuple[str, bytes]], **form: str):
    """POST /datasets with the given (filename, bytes) pairs; returns the raw response."""
    parts = [("files", (name, data, "application/octet-stream")) for name, data in files]
    return client.post("/api/v1/datasets", files=parts, data=form)


def wait_job(client: TestClient, job_id: str, timeout: float = 60.0) -> dict:
    """Poll a job until it is finished; returns its final JSON."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/jobs/{job_id}").json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError("the job did not finish in time")


def upload_ready(client: TestClient, files: list[tuple[str, bytes]] | None = None) -> str:
    """Upload, wait for the ingest job and return the new dataset id."""
    response = upload(client, files or [("sales.csv", sales_csv())])
    assert response.status_code == 202, response.text
    job = wait_job(client, response.json()["id"])
    assert job["status"] == "done", job
    return job["result"]["dataset_id"]
