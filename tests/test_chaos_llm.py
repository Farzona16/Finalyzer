"""Chaos tests: whatever the AI service does, the app ends in a readable state.

The failure is injected below the retry logic (``LiveProvider._call_litellm``), so the real
retry, gateway, Egress Gate, question pipeline and narrator all run.
"""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from app.state import CHOICE_KEY
from core.config import get_settings
from core.errors import LLMUnavailableError
from core.llm import providers
from core.llm.providers import LiveProvider, LLMResponse
from tests.fakes import make_settings
from tests.test_app_smoke import APP_FILE, TIMEOUT, assert_clean, texts

MAX_RETRIES = 2


class RateLimitError(Exception):
    """Named like LiteLLM's error so it is recognised as retryable."""

    status_code = 429


class InternalServerError(Exception):
    """Named like LiteLLM's error (HTTP 500)."""

    status_code = 500


class AuthenticationError(Exception):
    """A permanent failure: the key is wrong (HTTP 401)."""

    status_code = 401


def raise_(error: Exception):
    """A replacement for the raw model call that always fails with ``error``."""

    def call(self, messages, temperature):
        raise error

    return call


def reply(text: str):
    """A replacement for the raw model call that always returns ``text``."""

    def call(self, messages, temperature):
        return LLMResponse(text=text, tokens_in=5, tokens_out=5)

    return call


FAILURES = {
    "timeout": raise_(TimeoutError("timed out")),
    "rate_limit_429": raise_(RateLimitError("slow down")),
    "server_error_500": raise_(InternalServerError("boom")),
    "malformed_json": reply("Sure! Here is your answer: {oops"),
    "empty_output": reply(""),
    "wrong_key_401": raise_(AuthenticationError("bad key")),
}


@pytest.fixture
def live_settings(monkeypatch: pytest.MonkeyPatch):
    """A configuration with a key, retries enabled and instant back-off."""
    for name, value in {
        "LLM_MODE": "live", "LLM_API_KEY": "test-key", "LLM_MODEL": "fake-model",
        "APP_PASSWORD": "", "PRIVACY_MODE": "demo", "LLM_MAX_RETRIES": str(MAX_RETRIES),
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(providers, "backoff_delay", lambda attempt: 0.0)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize("name", list(FAILURES))
def test_app_stays_usable_when_the_ai_fails(
    name: str, live_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(LiveProvider, "_call_litellm", FAILURES[name])
    at = AppTest.from_file(APP_FILE, default_timeout=TIMEOUT).run()
    at.sidebar.radio(key=CHOICE_KEY).set_value("retail").run()
    assert_clean(at)

    at.text_input(key="fz_question").set_value("How many sales are there?")
    next(b for b in at.button if b.label == "Ask").click().run()
    assert not at.exception
    message = texts(at.info) + texts(at.warning) + texts(at.error)
    assert "AI" in message or "answer" in message.lower()
    assert "Traceback" not in message and "Error(" not in message

    at.button(key="fz_run_insights").click().run()
    assert not at.exception
    assert "Template-written" in texts(at.markdown)
    assert "Generated without AI" in texts(at.caption)

    assert "fz-card" in texts(at.markdown)  # the dashboard keeps working
    assert any(m.label == "Blocked calls" for m in at.metric)


@pytest.mark.parametrize(
    "error, retries",
    [
        (TimeoutError("t"), MAX_RETRIES),
        (RateLimitError("r"), MAX_RETRIES),
        (InternalServerError("s"), MAX_RETRIES),
        (AuthenticationError("a"), 0),
    ],
)
def test_transient_errors_are_retried_and_permanent_ones_are_not(
    error: Exception, retries: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = []

    def call(self, messages, temperature):
        attempts.append(1)
        raise error

    monkeypatch.setattr(LiveProvider, "_call_litellm", call)
    provider = LiveProvider(make_settings(llm_max_retries=MAX_RETRIES), sleep=lambda s: None)
    with pytest.raises(LLMUnavailableError) as caught:
        provider.complete([{"role": "user", "content": "hi"}], 0.0)
    assert len(attempts) == retries + 1
    assert "busy or not reachable" in caught.value.message or "rejected" in caught.value.message


def test_a_flaky_service_recovers_within_the_retry_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def call(self, messages, temperature):
        calls.append(1)
        if len(calls) < MAX_RETRIES + 1:
            raise RateLimitError("try later")
        return LLMResponse(text="ok", tokens_in=1, tokens_out=1)

    monkeypatch.setattr(LiveProvider, "_call_litellm", call)
    provider = LiveProvider(make_settings(llm_max_retries=MAX_RETRIES), sleep=lambda s: None)
    assert provider.complete([{"role": "user", "content": "hi"}], 0.0).text == "ok"
    assert len(calls) == MAX_RETRIES + 1
