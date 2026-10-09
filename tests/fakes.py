"""Test doubles: a scriptable, thread-safe fake LLM provider and a settings factory."""

from __future__ import annotations

import copy
import json
import threading
from collections.abc import Callable
from typing import Any

from core.config import Settings
from core.llm.base import Capabilities, LLMResponse

Reply = str | Exception | Callable[[list[dict[str, str]], float], str]


def make_settings(**overrides: Any) -> Settings:
    """Settings that ignore the real environment file. Defaults: demo mode, fake live model."""
    values: dict[str, Any] = {
        "privacy_mode": "demo",
        "llm_mode": "live",
        "llm_model": "fake-model",
        "llm_api_key": "test-key",
        "llm_max_retries": 0,
        "cache_identical_prompts": False,
        "provider_clearance": "anthropic:synthetic_only,grok:synthetic_only,fake:strict_ok",
        "session_token_budget": 200_000,
        "dataset_token_budget": 400_000,
        "daily_token_budget": 1_000_000,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def query_reply(
    sql: str, chart_hint: str | None = None, metric_ids: list[str] | None = None
) -> str:
    """A model reply of intent ``query``."""
    return json.dumps(
        {
            "intent": "query",
            "sql": sql,
            "clarification": None,
            "chart_hint": chart_hint,
            "metric_ids": metric_ids or [],
        }
    )


def clarify_reply(question: str) -> str:
    """A model reply of intent ``clarify``."""
    return json.dumps({"intent": "clarify", "sql": None, "clarification": question})


def refuse_reply(text: str = "I can only read data.") -> str:
    """A model reply of intent ``refuse``."""
    return json.dumps({"intent": "refuse", "sql": None, "clarification": text})


class FakeProvider:
    """Returns scripted replies in call order and records exactly what it received.

    ``replies`` may hold strings, exceptions (raised) or callables ``(messages, temperature)``.
    When the script runs out, the last reply is repeated.
    """

    external = True
    api_base = ""
    capabilities = Capabilities()

    def __init__(
        self, replies: list[Reply] | None = None, model: str = "fake-model", name: str = "fake"
    ) -> None:
        self.model = model
        self.name = name
        self._replies = list(replies or [])
        self._lock = threading.Lock()
        self.calls: list[list[dict[str, str]]] = []
        self.temperatures: list[float] = []
        self.kwargs: list[dict[str, Any]] = []

    def complete(
        self, messages: list[dict[str, str]], temperature: float, **kwargs: Any
    ) -> LLMResponse:
        """Record the call and return the next scripted reply."""
        with self._lock:
            index = len(self.calls)
            self.calls.append(copy.deepcopy(messages))
            self.temperatures.append(temperature)
            self.kwargs.append(dict(kwargs))
            reply = self._replies[min(index, len(self._replies) - 1)] if self._replies else "{}"
        if isinstance(reply, Exception):
            raise reply
        text = reply(messages, temperature) if callable(reply) else reply
        return LLMResponse(text=text, tokens_in=10, tokens_out=5)
