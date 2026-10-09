"""Chaos tests: whatever the AI service does, the app ends in a readable state.

The failure is injected at the HTTP transport of a real ``OpenAICompatProvider``, so the real
retry, circuit breaker, chain, Egress Gate, question pipeline and narrator all run.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from app.state import CHOICE_KEY
from core.config import get_settings
from core.errors import LLMUnavailableError
from core.llm import gateway as gateway_module
from core.llm.openai_compat import OpenAICompatProvider
from tests.test_app_smoke import APP_FILE, TIMEOUT, assert_clean, texts

MAX_RETRIES = 2


def status(code: int, body: str = "{}") -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(code, text=body)


def content(text: str) -> Callable[[httpx.Request], httpx.Response]:
    """A 200 answer with ``text`` as the message and no tool call."""
    return lambda request: httpx.Response(
        200, json={"choices": [{"message": {"role": "assistant", "content": text}}],
                   "usage": {"prompt_tokens": 5, "completion_tokens": 5}},
    )


def timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


FAILURES: dict[str, Callable[[httpx.Request], httpx.Response]] = {
    "timeout": timeout,
    "rate_limit_429": status(429, '{"error": "slow down"}'),
    "server_error_500": status(500, '{"error": "boom"}'),
    "malformed_json": content("Sure! Here is your answer: {oops"),
    "empty_output": content(""),
    "wrong_key_401": status(401, '{"error": "bad key"}'),
    "credits_402": status(402, '{"error": "payment required"}'),
}


def provider_with(handler: Callable[[httpx.Request], httpx.Response]) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        models={"main": "grok-test"},
        api_key="test-key",
        api_base="https://api.x.ai/v1",
        max_retries=MAX_RETRIES,
        allowed_hosts=frozenset({"api.x.ai"}),
        transport=httpx.MockTransport(handler),
        sleep=lambda seconds: None,
    )


@pytest.fixture
def live_settings(monkeypatch: pytest.MonkeyPatch):
    """A configuration with a Grok key and model; retries enabled."""
    for name, value in {
        "LLM_MODE": "live", "XAI_API_KEY": "test-key", "GROK_MODEL_MAIN": "grok-test",
        "LLM_PROVIDER_CHAIN": "grok,offline", "APP_PASSWORD": "", "PRIVACY_MODE": "demo",
        "LLM_MAX_RETRIES": str(MAX_RETRIES),
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize("name", list(FAILURES))
def test_app_stays_usable_when_the_ai_fails(
    name: str, live_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = provider_with(FAILURES[name])
    monkeypatch.setattr(gateway_module, "build_providers", lambda settings, runtime: [provider])
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
    "handler, requests",
    [
        (timeout, MAX_RETRIES + 1),
        (status(429), MAX_RETRIES + 1),
        (status(500), MAX_RETRIES + 1),
        (status(401), 1),
        (status(402), 1),
    ],
)
def test_transient_errors_are_retried_and_permanent_ones_are_not(
    handler: Callable[[httpx.Request], httpx.Response], requests: int
) -> None:
    seen: list[httpx.Request] = []

    def counting(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    with pytest.raises(LLMUnavailableError) as caught:
        provider_with(counting).complete([{"role": "user", "content": "hi"}], 0.0)
    assert len(seen) == requests
    assert any(word in caught.value.message for word in ("busy or not reachable", "key", "credit"))


def test_a_flaky_service_recovers_within_the_retry_budget() -> None:
    seen: list[httpx.Request] = []

    def flaky(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) <= MAX_RETRIES:
            return httpx.Response(429, text="{}")
        return content("ok")(request)

    assert provider_with(flaky).complete([{"role": "user", "content": "hi"}], 0.0).text == "ok"
    assert len(seen) == MAX_RETRIES + 1
