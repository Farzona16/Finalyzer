"""Settings validation and derived properties."""

import pytest

from core.config import Settings, load_settings
from core.errors import UserFacingError, describe_exception


def make_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    """Build settings from a clean environment (ignoring any local .env file)."""
    for key in list(Settings.model_fields):
        monkeypatch.delenv(key.upper(), raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(monkeypatch)
    assert settings.privacy_mode == "demo"
    assert settings.sql_candidates == 3
    assert settings.result_row_limit == 10_000
    assert settings.k_anon_min == 5


def test_local_mode_requires_api_base(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="LLM_API_BASE"):
        make_settings(monkeypatch, PRIVACY_MODE="local")


def test_local_mode_error_is_user_facing(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(Settings.model_fields):
        monkeypatch.delenv(key.upper(), raising=False)
    monkeypatch.setenv("PRIVACY_MODE", "local")
    monkeypatch.chdir("tests")  # no .env file is picked up here
    with pytest.raises(UserFacingError) as info:
        load_settings()
    assert "LLM_API_BASE" in info.value.message


def test_local_mode_with_api_base_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(
        monkeypatch,
        PRIVACY_MODE="local",
        LLM_API_BASE="http://localhost:11434",
        LLM_MODEL="ollama/some-model",
    )
    assert settings.effective_llm_mode == "live"


def test_offline_when_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(monkeypatch, LLM_MODEL="provider/model")
    assert settings.effective_llm_mode == "offline"


def test_live_with_key_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(monkeypatch, LLM_MODEL="provider/model", LLM_API_KEY="secret")
    assert settings.effective_llm_mode == "live"


def test_explicit_offline_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(
        monkeypatch, LLM_MODEL="provider/model", LLM_API_KEY="secret", LLM_MODE="offline"
    )
    assert settings.effective_llm_mode == "offline"


def test_category_values_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    assert make_settings(monkeypatch, PRIVACY_MODE="demo").category_values_allowed
    assert not make_settings(monkeypatch, PRIVACY_MODE="cloud").category_values_allowed
    cloud_on = make_settings(monkeypatch, PRIVACY_MODE="cloud", SEND_CATEGORY_VALUES="true")
    assert cloud_on.category_values_allowed


def test_result_rows_to_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    assert make_settings(monkeypatch, PRIVACY_MODE="cloud").max_result_rows_to_llm == 0
    assert make_settings(monkeypatch, PRIVACY_MODE="demo").max_result_rows_to_llm == 50


def test_unknown_exception_gets_reference_id() -> None:
    text = describe_exception(RuntimeError("boom"))
    assert text.startswith("Something went wrong (ref: ")
    assert len(text.split("ref: ")[1].rstrip(")")) == 8


def test_unknown_exception_log_has_reference_and_no_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("ERROR"):
        text = describe_exception(RuntimeError("secret customer value 12345"))
    ref = text.split("ref: ")[1].rstrip(")")
    assert f"ref={ref}" in caplog.text and "RuntimeError" in caplog.text
    assert "secret customer value" not in caplog.text


def test_user_facing_message_is_shown_as_is() -> None:
    assert describe_exception(UserFacingError("Bad file")) == "Bad file"
