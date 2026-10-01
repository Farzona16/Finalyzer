"""S15: the backend refuses to start with a missing or weak configuration."""

from __future__ import annotations

import base64

import pytest
from app.config import BackendSettings, ConfigError, load_settings
from pydantic import ValidationError

from tests.conftest import SECRET


def build(**overrides: object) -> BackendSettings:
    values: dict[str, object] = {"app_secret_key": SECRET}
    values.update(overrides)
    return BackendSettings(_env_file=None, **values)  # type: ignore[arg-type]


def test_a_valid_configuration_loads() -> None:
    settings = build()
    assert settings.app_env == "development"
    assert settings.secure_cookies is False
    assert settings.allowed_modes == ("demo", "cloud", "local")


@pytest.mark.parametrize("secret", ["", "short", "x" * 40, "changeme", "a" * 31])
def test_missing_or_weak_secret_key_is_refused(secret: str) -> None:
    with pytest.raises(ValidationError):
        build(app_secret_key=secret)


def test_load_settings_error_never_contains_the_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = "tooshort-but-secret-value"
    monkeypatch.setitem(BackendSettings.model_config, "env_file", None)
    monkeypatch.setenv("APP_SECRET_KEY", bad)
    with pytest.raises(ConfigError) as caught:
        load_settings()
    assert bad not in str(caught.value)
    assert "app_secret_key" in str(caught.value)


def test_missing_secret_in_the_environment_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(BackendSettings.model_config, "env_file", None)
    monkeypatch.delenv("APP_SECRET_KEY", raising=False)
    with pytest.raises(ConfigError):
        load_settings()


def test_persistent_storage_needs_a_valid_master_key() -> None:
    with pytest.raises(ValidationError):
        build(storage_mode="persistent")
    with pytest.raises(ValidationError):
        build(storage_mode="persistent", app_master_key="not base64 !!")
    with pytest.raises(ValidationError):
        build(storage_mode="persistent", app_master_key=base64.b64encode(b"short").decode())
    key = base64.b64encode(b"k" * 32).decode()
    assert build(storage_mode="persistent", app_master_key=key).storage_mode == "persistent"


def test_production_refuses_insecure_cookies() -> None:
    with pytest.raises(ValidationError):
        build(app_env="production", cookie_secure=False)
    assert build(app_env="production").secure_cookies is True


def test_privacy_modes_are_validated() -> None:
    with pytest.raises(ValidationError):
        build(allowed_privacy_modes="demo,bogus")
    with pytest.raises(ValidationError):
        build(allowed_privacy_modes="")
    with pytest.raises(ValidationError):
        build(allowed_privacy_modes="local", privacy_mode="demo")
    assert build(allowed_privacy_modes="local, demo").allowed_modes == ("local", "demo")


def test_render_style_database_urls_get_a_driver() -> None:
    assert build(database_url="postgres://u:p@h/db").database_url.startswith(
        "postgresql+psycopg://"
    )
