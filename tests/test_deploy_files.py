"""The deployment files agree with each other and with the app."""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

from app.theme import PRIMARY

ROOT = Path(__file__).resolve().parents[1]


def render_service() -> dict:
    """The only service declared in render.yaml."""
    document = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    assert len(document["services"]) == 1
    return document["services"][0]


def test_render_service_is_a_docker_web_service_with_a_health_check() -> None:
    service = render_service()
    assert service["type"] == "web"
    assert service["runtime"] == "docker"
    assert (ROOT / service["dockerfilePath"]).is_file()
    assert service["healthCheckPath"] == "/healthz"
    assert service["plan"] != "free"


def test_render_secrets_are_not_stored_and_modes_are_set() -> None:
    env = {item["key"]: item for item in render_service()["envVars"]}
    for secret in ("LLM_API_KEY", "LLM_MODEL", "APP_SECRET_KEY", "APP_MASTER_KEY", "DATABASE_URL"):
        assert env[secret].get("sync") is False
        assert "value" not in env[secret]
    assert env["PRIVACY_MODE"]["value"] == "demo"
    assert env["LLM_MODE"]["value"] == "offline"
    assert env["STORAGE_MODE"]["value"] == "ephemeral"


def test_render_variables_are_known_settings() -> None:
    known = {
        line.split("=")[0]
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    }
    declared = {item["key"] for item in render_service()["envVars"]}
    assert declared <= known


def test_dockerfile_runs_as_non_root_on_the_render_port() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM python:3.12-slim" in text
    assert "USER finalyzer" in text
    assert "${PORT:-8000}" in text
    assert "uvicorn" in text
    assert "--workers 1" in text
    assert "streamlit run" not in text.lower()


def test_dockerignore_keeps_secrets_out_of_the_image() -> None:
    lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in lines
    assert ".venv/" in lines
    assert "app/" in lines  # Streamlit stays out of the image (U12)


def test_streamlit_config_matches_the_app() -> None:
    config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert config["server"]["maxUploadSize"] == 50
    assert config["server"]["enableXsrfProtection"] is True
    assert config["browser"]["gatherUsageStats"] is False
    assert config["theme"]["primaryColor"] == PRIMARY
