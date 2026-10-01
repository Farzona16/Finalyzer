"""Same-origin SPA serving (D4) and database migrations (D13)."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app.db import downgrade_to_base, make_engine, upgrade_to_head
from app.models import Base
from fastapi.testclient import TestClient
from sqlalchemy import inspect

# ---------------------------------------------------------------- SPA


@pytest.fixture
def built_client(make_settings, make_client, tmp_path: Path) -> TestClient:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app shell</html>", encoding="utf-8")
    (dist / "assets" / "app-123.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "robots.txt").write_text("User-agent: *", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("TOP-SECRET", encoding="utf-8")
    return make_client(frontend_dist=dist)


def test_the_shell_is_served_for_the_root_and_client_routes(built_client: TestClient) -> None:
    for path in ("/", "/dashboard", "/data/upload/deep/link"):
        response = built_client.get(path)
        assert response.status_code == 200
        assert "app shell" in response.text
        assert response.headers["cache-control"] == "no-cache"


def test_hashed_assets_are_cached_forever_and_other_files_are_not(built_client: TestClient) -> None:
    asset = built_client.get("/assets/app-123.js")
    assert asset.status_code == 200
    assert "immutable" in asset.headers["cache-control"]
    assert "immutable" not in built_client.get("/robots.txt").headers.get("cache-control", "")


@pytest.mark.parametrize(
    "path",
    [
        "/../secret.txt",
        "/..%2fsecret.txt",
        "/%2e%2e/secret.txt",
        "/assets/../../secret.txt",
        "/assets/%2e%2e/%2e%2e/secret.txt",
        "/....//secret.txt",
        "/..\\secret.txt",
    ],
)
def test_path_traversal_never_leaks_files(built_client: TestClient, path: str) -> None:
    response = built_client.get(path)
    assert "TOP-SECRET" not in response.text


def test_unknown_api_paths_do_not_fall_back_to_the_shell(built_client: TestClient) -> None:
    for path in ("/api", "/api/", "/api/v2/anything", "/api/v1/missing"):
        response = built_client.get(path)
        assert response.status_code == 404, path
        assert "app shell" not in response.text


def test_a_friendly_placeholder_is_shown_before_the_first_build(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Finalyzer" in response.text
    assert "yigʻilmagan" in response.text


def test_the_spa_responses_carry_the_security_headers(built_client: TestClient) -> None:
    response = built_client.get("/")
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "cache-control" in response.headers


# ---------------------------------------------------------------- migrations


@pytest.fixture
def migrated_engine(make_settings):
    engine = make_engine(make_settings())
    upgrade_to_head(engine)
    yield engine
    engine.dispose()


def test_upgrade_creates_every_model_table(migrated_engine) -> None:
    tables = set(inspect(migrated_engine).get_table_names())
    assert set(Base.metadata.tables) <= tables
    assert "alembic_version" in tables


def test_the_migration_matches_the_models(migrated_engine) -> None:
    """Models and migrations must not drift apart."""
    with migrated_engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []


def test_downgrade_removes_everything_and_upgrade_works_again(migrated_engine) -> None:
    downgrade_to_base(migrated_engine)
    assert set(inspect(migrated_engine).get_table_names()) <= {"alembic_version"}
    upgrade_to_head(migrated_engine)
    assert set(Base.metadata.tables) <= set(inspect(migrated_engine).get_table_names())


def test_upgrading_twice_is_harmless(migrated_engine) -> None:
    upgrade_to_head(migrated_engine)


def test_sqlite_enforces_foreign_keys_and_wal(migrated_engine) -> None:
    with migrated_engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "wal"
