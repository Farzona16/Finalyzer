"""Mocked-transport tests for the HTTP providers and the chain (v3.1-Z phase V1).

No network: Grok runs on ``httpx.MockTransport`` and Claude (SDK) on ``httpx2.MockTransport``.
Response bodies come from ``tests/data/provider_samples`` (shapes from the provider docs).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest

from core.errors import LLMUnavailableError
from core.llm.anthropic_provider import AnthropicProvider
from core.llm.audit import AuditLog
from core.llm.base import (
    BudgetExceededError,
    CircuitOpenError,
    CreditsExhaustedError,
    ForcedToolUnsupportedError,
    ProviderAuthError,
    ProviderTransientError,
    ToolSpec,
)
from core.llm.gateway import TEMPLATE_MODE_MESSAGE, LLMGateway
from core.llm.http_base import CircuitBreaker, backoff_delay, parse_retry_after
from core.llm.openai_compat import EgressHostBlockedError, OpenAICompatProvider
from core.llm.registry import LLMRuntime, build_providers, verify_models
from core.query.nl2sql import NLAnswer
from tests.fakes import make_settings

SAMPLES = Path(__file__).parent / "data" / "provider_samples"
SECRET = "sk-test-SECRET-9f8e7d"
TOOL = ToolSpec(
    name="submit_nl2sql",
    description="Return the SQL.",
    schema={"type": "object", "properties": {"sql": {"type": "string"}}},
)
MESSAGES = [
    {"role": "system", "content": "You translate business questions into SQL."},
    {"role": "user", "content": "How many rows?"},
]


def sample(name: str) -> Any:
    text = (SAMPLES / name).read_text(encoding="utf-8")
    return json.loads(text) if name.endswith(".json") else text


Handler = Callable[[Any], Any]


class Recorder:
    """Scripted responses in order (the last repeats); records every request body."""

    def __init__(self, module: Any, *responses: Any) -> None:
        self.module = module
        self.responses = list(responses)
        self.requests: list[Any] = []

    def __call__(self, request: Any) -> Any:
        self.requests.append(request)
        item = self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(request)
        status, body, headers = item
        if isinstance(body, str):
            return self.module.Response(status, text=body, headers=headers or {})
        return self.module.Response(status, json=body, headers=headers or {})

    def body(self, index: int = -1) -> dict[str, Any]:
        return json.loads(self.requests[index].content)


def grok(
    recorder: Recorder, sleeps: list[float] | None = None, **kwargs: Any
) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        models={"main": "grok-test-main", "fast": "grok-test-fast"},
        api_key=SECRET,
        api_base=kwargs.pop("api_base", "https://api.x.ai/v1"),
        allowed_hosts=frozenset({"api.x.ai"}),
        transport=httpx.MockTransport(recorder),
        sleep=(sleeps if sleeps is not None else []).append,
        **kwargs,
    )


def claude(
    recorder: Recorder, sleeps: list[float] | None = None, **kwargs: Any
) -> AnthropicProvider:
    return AnthropicProvider(
        models={"main": "claude-test-main", "fast": "claude-test-fast"},
        api_key=SECRET,
        allowed_hosts=frozenset({"api.anthropic.com"}),
        transport=httpx2.MockTransport(recorder),
        sleep=(sleeps if sleeps is not None else []).append,
        **kwargs,
    )


def grok_ok(sql: str = "SELECT 1") -> tuple[int, dict[str, Any], None]:
    data = sample("grok_tool_call.json")
    args = json.dumps({"intent": "query", "sql": sql})
    data["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = args
    return 200, data, None


# ---------------------------------------------------------------------- Grok (httpx)


def test_grok_forced_tool_request_and_parsing() -> None:
    rec = Recorder(httpx, (200, sample("grok_tool_call.json"), None))
    response = grok(rec).complete(MESSAGES, 0.0, tool=TOOL, tier="fast", max_tokens=300)
    body = rec.body()
    assert rec.requests[0].url == "https://api.x.ai/v1/chat/completions"
    assert rec.requests[0].headers["authorization"] == f"Bearer {SECRET}"
    assert body["model"] == "grok-test-fast" and body["max_completion_tokens"] == 300
    assert "max_tokens" not in body
    assert body["tool_choice"] == {"type": "function", "function": {"name": "submit_nl2sql"}}
    assert body["tools"][0]["function"]["parameters"] == TOOL.schema
    assert response.tool_args == {
        "intent": "query", "sql": "SELECT COUNT(*) AS n FROM data", "clarification": None,
    }
    assert (response.tokens_in, response.tokens_out) == (380, 35)
    assert response.provider == "grok" and response.model == "grok-test-main"


def test_grok_plain_text_from_docs_example() -> None:
    rec = Recorder(httpx, (200, sample("grok_chat.json"), None))
    response = grok(rec).complete(MESSAGES, 0.0)
    assert response.text == "101 multiplied by 3 is 303."
    assert (response.tokens_in, response.tokens_out) == (32, 9)
    assert "tools" not in rec.body()


def test_grok_falls_back_to_response_format_then_turns_forced_tool_off() -> None:
    content = json.dumps({"sql": "SELECT 2"})
    reply = {"choices": [{"message": {"role": "assistant", "content": content}}], "usage": {}}
    rec = Recorder(
        httpx,
        (400, {"error": "tool_choice is not supported for this model"}, None),
        (200, reply, None),
    )
    provider = grok(rec)
    response = provider.complete(MESSAGES, 0.0, tool=TOOL)
    assert response.tool_args == {"sql": "SELECT 2"}
    assert rec.body(1)["response_format"]["json_schema"]["name"] == "submit_nl2sql"
    assert "tools" not in rec.body(1)
    assert provider.capabilities.forced_tool  # schema output still works

    rec.responses = [(400, {"error": "response_format json_schema not supported"}, None)]
    with pytest.raises(ForcedToolUnsupportedError):
        provider.complete(MESSAGES, 0.0, tool=TOOL)
    assert not provider.capabilities.forced_tool


def test_grok_429_honours_retry_after() -> None:
    sleeps: list[float] = []
    rec = Recorder(httpx, (429, {"error": "slow down"}, {"retry-after": "3"}), grok_ok())
    response = grok(rec, sleeps, max_retries=2).complete(MESSAGES, 0.0, tool=TOOL)
    assert response.tool_args == {"intent": "query", "sql": "SELECT 1"}
    assert sleeps == [3.0] and len(rec.requests) == 2


def test_grok_5xx_is_retried_then_reported_as_transient() -> None:
    sleeps: list[float] = []
    rec = Recorder(httpx, (503, {"error": "unavailable"}, None))
    provider = grok(rec, sleeps, max_retries=2)
    with pytest.raises(ProviderTransientError) as caught:
        provider.complete(MESSAGES, 0.0)
    assert len(rec.requests) == 3 and len(sleeps) == 2
    assert "busy or not reachable" in caught.value.message
    assert provider.breaker.failures == 1


def test_grok_timeout_is_retried() -> None:
    rec = Recorder(httpx, httpx.ReadTimeout("slow"), grok_ok())
    response = grok(rec, max_retries=1).complete(MESSAGES, 0.0, tool=TOOL)
    assert response.tool_args is not None and len(rec.requests) == 2


def test_grok_permanent_errors_are_not_retried() -> None:
    for status, body, error in [
        (401, {"error": "Incorrect API key provided"}, ProviderAuthError),
        (402, {"error": "payment required"}, CreditsExhaustedError),
        (429, {"error": "You have exceeded your spending limit"}, CreditsExhaustedError),
    ]:
        rec = Recorder(httpx, (status, body, None))
        with pytest.raises(error):
            grok(rec, max_retries=2).complete(MESSAGES, 0.0)
        assert len(rec.requests) == 1, status


def test_circuit_opens_then_half_opens_and_closes() -> None:
    now = [0.0]
    breaker = CircuitBreaker(threshold=2, reset_after_s=60, clock=lambda: now[0])
    rec = Recorder(httpx, (500, {"error": "boom"}, None))
    provider = grok(rec, max_retries=0, breaker=breaker)
    for _ in range(2):
        with pytest.raises(ProviderTransientError):
            provider.complete(MESSAGES, 0.0)
    assert breaker.state == "open"
    with pytest.raises(CircuitOpenError):
        provider.complete(MESSAGES, 0.0)
    assert len(rec.requests) == 2  # the open circuit made no request

    now[0] = 61.0
    assert breaker.state == "half_open"
    rec.responses = [(200, sample("grok_chat.json"), None)]
    assert provider.complete(MESSAGES, 0.0).text
    assert breaker.state == "closed"


def test_half_open_failure_reopens_the_circuit() -> None:
    now = [0.0]
    breaker = CircuitBreaker(threshold=1, reset_after_s=10, clock=lambda: now[0])
    rec = Recorder(httpx, (500, {"error": "boom"}, None))
    provider = grok(rec, max_retries=0, breaker=breaker)
    with pytest.raises(ProviderTransientError):
        provider.complete(MESSAGES, 0.0)
    now[0] = 11.0
    with pytest.raises(ProviderTransientError):
        provider.complete(MESSAGES, 0.0)
    assert breaker.state == "open"


def test_grok_streaming_parses_sse_until_done() -> None:
    rec = Recorder(httpx, (200, sample("grok_stream.sse"), {"content-type": "text/event-stream"}))
    chunks = list(grok(rec).stream(MESSAGES, 0.0, max_tokens=50))
    assert chunks == ["Hel", "lo!"]
    assert rec.body()["stream"] is True


def test_grok_stream_retries_before_the_first_chunk() -> None:
    rec = Recorder(
        httpx,
        (503, {"error": "unavailable"}, None),
        (200, sample("grok_stream.sse"), {"content-type": "text/event-stream"}),
    )
    assert "".join(grok(rec, max_retries=1).stream(MESSAGES, 0.0)) == "Hello!"


def test_host_outside_the_allowlist_is_blocked_before_sending() -> None:
    rec = Recorder(httpx, grok_ok())
    provider = grok(rec, api_base="https://evil.example.com/v1")
    with pytest.raises(EgressHostBlockedError):
        provider.complete(MESSAGES, 0.0)
    assert rec.requests == []


def test_no_secrets_or_payload_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    payload = "secret customer value 4242"
    messages = [{"role": "user", "content": payload}]
    for status in (429, 500, 401):
        rec = Recorder(httpx, (status, {"error": f"echo {payload} {SECRET}"}, None))
        with pytest.raises(LLMUnavailableError):
            grok(rec, max_retries=1).complete(messages, 0.0)
    rec2 = Recorder(httpx2, (529, {"type": "error", "error": {"type": "overloaded_error",
                                                              "message": payload}}, None))
    with pytest.raises(ProviderTransientError):
        claude(rec2, max_retries=1).complete(messages, 0.0)
    assert "llm_call_failed" in caplog.text
    assert SECRET not in caplog.text and payload not in caplog.text
    assert SECRET not in repr(grok(rec)) and SECRET not in repr(claude(rec2))


def test_backoff_and_retry_after_are_capped() -> None:
    assert backoff_delay(0, jitter=lambda: 0.5) == 1.0
    assert backoff_delay(10, jitter=lambda: 0.5) == 20.0
    assert parse_retry_after("120") == 20.0
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") is None


# ---------------------------------------------------------------------- Claude (SDK)


def test_claude_forced_tool_request_and_parsing() -> None:
    rec = Recorder(httpx2, (200, sample("anthropic_tool_use.json"), None))
    response = claude(rec).complete(MESSAGES, 0.3, tool=TOOL, tier="main", max_tokens=700)
    request = rec.requests[0]
    body = rec.body()
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == SECRET
    assert body["tool_choice"] == {"type": "tool", "name": "submit_nl2sql"}
    assert body["tools"][0]["input_schema"] == TOOL.schema
    assert body["system"] == [{
        "type": "text", "text": MESSAGES[0]["content"], "cache_control": {"type": "ephemeral"},
    }]
    assert body["messages"] == [MESSAGES[1]]
    assert "temperature" not in body and body["max_tokens"] == 700
    assert response.tool_args == {
        "intent": "query", "sql": "SELECT COUNT(*) AS n FROM data", "clarification": None,
    }
    assert (response.tokens_in, response.tokens_out) == (710, 42)
    assert response.provider == "anthropic"


def test_claude_model_that_refuses_forced_tools_gets_auto_and_strict() -> None:
    rec = Recorder(
        httpx2,
        (400, sample("anthropic_error_forced_tool.json"), None),
        (200, sample("anthropic_tool_use.json"), None),
    )
    provider = claude(rec)
    assert provider.complete(MESSAGES, 0.0, tool=TOOL).tool_args is not None
    retry = rec.body(1)
    assert retry["tool_choice"] == {"type": "auto"} and retry["tools"][0]["strict"] is True
    assert "submit_nl2sql" in retry["messages"][-1]["content"]
    provider.complete(MESSAGES, 0.0, tool=TOOL)
    assert rec.body(2)["tool_choice"] == {"type": "auto"}  # remembered per model
    assert len(rec.requests) == 3


def test_claude_429_retry_after_and_529_overloaded() -> None:
    sleeps: list[float] = []
    rec = Recorder(
        httpx2,
        (429, sample("anthropic_error_rate_limit.json"), {"retry-after": "2"}),
        (529, {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
         None),
        (200, sample("anthropic_tool_use.json"), None),
    )
    response = claude(rec, sleeps, max_retries=2).complete(MESSAGES, 0.0, tool=TOOL)
    assert response.tool_args is not None
    assert len(rec.requests) == 3 and sleeps[0] == 2.0


def test_claude_credit_error_is_not_retried() -> None:
    rec = Recorder(httpx2, (400, sample("anthropic_error_credit.json"), None))
    with pytest.raises(CreditsExhaustedError):
        claude(rec, max_retries=2).complete(MESSAGES, 0.0)
    assert len(rec.requests) == 1


def test_claude_timeout_is_transient() -> None:
    rec = Recorder(httpx2, httpx2.ReadTimeout("slow"))
    with pytest.raises(ProviderTransientError):
        claude(rec, max_retries=1).complete(MESSAGES, 0.0)
    assert len(rec.requests) == 2


def test_claude_streaming_from_docs_example() -> None:
    rec = Recorder(httpx2, (200, sample("anthropic_stream.sse"),
                            {"content-type": "text/event-stream"}))
    assert list(claude(rec).stream(MESSAGES, 0.0, max_tokens=50)) == ["Hello", "!"]
    assert rec.body()["stream"] is True


def test_claude_mid_stream_overloaded_error_is_transient() -> None:
    rec = Recorder(httpx2, (200, sample("anthropic_stream_error.sse"),
                            {"content-type": "text/event-stream"}))
    with pytest.raises(ProviderTransientError):
        list(claude(rec, max_retries=1).stream(MESSAGES, 0.0))
    assert len(rec.requests) == 2  # retried: no chunk had reached the caller


def test_claude_host_allowlist_blocks_before_sending() -> None:
    rec = Recorder(httpx2, (200, sample("anthropic_tool_use.json"), None))
    provider = claude(rec, api_base="https://proxy.example.com")
    with pytest.raises(EgressHostBlockedError):
        provider.complete(MESSAGES, 0.0)
    assert rec.requests == []


def test_list_models_and_unknown_model_is_skipped() -> None:
    runtime = LLMRuntime()
    good = claude(Recorder(httpx2, (200, sample("anthropic_models.json"), None)))
    bad = grok(Recorder(httpx, (200, sample("grok_models.json"), None)))
    bad.models["deep"] = "grok-retired"
    report = verify_models([good, bad], runtime)
    assert report["anthropic"]["unknown"] == [] and report["grok"]["unknown"] == ["grok-retired"]
    assert not runtime.is_skipped("anthropic") and runtime.is_skipped("grok")


def test_model_check_with_a_bad_key_disables_the_provider() -> None:
    runtime = LLMRuntime()
    provider = grok(Recorder(httpx, (401, {"error": "bad key"}, None)))
    assert verify_models([provider], runtime)["grok"]["checked"] is False
    assert runtime.is_skipped("grok")


# ---------------------------------------------------------------------- chain


def chain(*providers: Any, runtime: LLMRuntime | None = None, **settings: Any) -> LLMGateway:
    return LLMGateway(make_settings(**settings), AuditLog(), providers=list(providers),
                      runtime=runtime or LLMRuntime())


def test_chain_falls_through_to_the_next_provider() -> None:
    down = Recorder(httpx2, (529, {"type": "error", "error": {"type": "overloaded_error",
                                                              "message": "Overloaded"}}, None))
    up = Recorder(httpx, grok_ok("SELECT COUNT(*) AS n FROM data"))
    gateway = chain(claude(down, max_retries=0), grok(up))
    value, response = gateway.complete_json_meta("nl2sql", MESSAGES, NLAnswer)
    assert value.sql == "SELECT COUNT(*) AS n FROM data"
    assert response.provider == "grok"
    events = gateway.audit.events
    assert [e.provider_model.split(":")[0] for e in events] == ["anthropic", "grok"]
    assert events[0].error == "ProviderTransientError" and not events[1].error


def test_when_every_provider_fails_the_message_says_template_mode() -> None:
    down = Recorder(httpx, (500, {"error": "boom"}, None))
    gateway = chain(grok(down, max_retries=0))
    with pytest.raises(LLMUnavailableError) as caught:
        gateway.complete_text("narrate", MESSAGES)
    assert TEMPLATE_MODE_MESSAGE in caught.value.message


def test_credits_exhausted_disables_the_provider_for_the_session() -> None:
    runtime = LLMRuntime()
    broke = Recorder(httpx2, (400, sample("anthropic_error_credit.json"), None))
    up = Recorder(httpx, (200, sample("grok_chat.json"), None))
    gateway = chain(claude(broke), grok(up), runtime=runtime)
    assert gateway.complete_text("narrate", MESSAGES)
    assert runtime.is_skipped("anthropic")
    gateway.complete_text("narrate", MESSAGES + [{"role": "user", "content": "again"}])
    assert len(broke.requests) == 1 and len(up.requests) == 2
    states = {s["name"]: s["state"] for s in gateway.status()["providers"]}
    assert states == {"anthropic": "credits_exhausted", "grok": "ok"}


def test_budget_hard_stop_raises_before_any_request() -> None:
    rec = Recorder(httpx, grok_ok())
    gateway = chain(grok(rec), session_token_budget=5)
    with pytest.raises(BudgetExceededError) as caught:
        gateway.complete_text("narrate", MESSAGES)
    assert caught.value.scope == "session" and rec.requests == []
    assert gateway.audit.events[-1].blocked


def test_real_data_only_goes_to_a_cleared_provider() -> None:
    claude_rec = Recorder(httpx2, (200, sample("anthropic_tool_use.json"), None))
    grok_rec = Recorder(httpx, (200, sample("grok_chat.json"), None))
    gateway = LLMGateway(
        make_settings(privacy_mode="cloud",
                      provider_clearance="anthropic:synthetic_only,grok:strict_ok"),
        AuditLog(), providers=[claude(claude_rec), grok(grok_rec)], data_origin="real",
    )
    assert gateway.complete_text("narrate", MESSAGES) == "101 multiplied by 3 is 303."
    assert claude_rec.requests == [] and len(grok_rec.requests) == 1


def test_offline_mode_builds_no_provider() -> None:
    settings = make_settings(llm_mode="offline", anthropic_api_key=SECRET,
                             claude_model_main="claude-test-main")
    gateway = LLMGateway(settings, AuditLog())
    assert gateway.is_offline and gateway.primary_model == "offline"
    assert build_providers(settings, LLMRuntime()) == []


def test_registry_builds_configured_links_in_chain_order() -> None:
    settings = make_settings(
        llm_provider_chain="grok,anthropic,offline",
        anthropic_api_key=SECRET, claude_model_main="claude-test-main",
        xai_api_key=SECRET, grok_model_main="grok-test-main", llm_model="", llm_api_key="",
    )
    providers = build_providers(settings, LLMRuntime())
    assert [p.name for p in providers] == ["grok", "anthropic"]
    assert all(SECRET not in repr(p) for p in providers)
