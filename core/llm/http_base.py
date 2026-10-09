"""``HttpProviderBase``: what every HTTP provider shares (spec 3.1).

Timeout, retry with exponential backoff + jitter (honouring ``retry-after``), a circuit
breaker and error classification. A concrete provider only maps a request to the wire format
and a response back (``_send`` and ``_send_stream``). Budgets and the audit record live in the
Egress Gate, which wraps every provider.

Log lines carry the provider name, HTTP status and error type only: never the key, the
payload or the provider's error text (which can echo the payload).
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from core.llm.base import (
    Capabilities,
    CircuitOpenError,
    CreditsExhaustedError,
    LLMResponse,
    ProviderAuthError,
    ProviderError,
    ProviderRejectedError,
    ProviderTransientError,
    Tier,
    ToolSpec,
)

logger = logging.getLogger("finalyzer.llm")

MAX_BACKOFF_S = 20.0
MAX_RETRY_AFTER_S = 20.0
# Anthropic answers 400 (spend limit) or 429 (spend cap, no retry-after) when money runs out;
# retrying those only burns the time budget.
CREDIT_WORDS = ("credit balance", "credits", "insufficient_quota", "quota", "billing",
                "spending limit", "spend limit", "spend cap", "payment required")


class HttpStatusError(Exception):
    """A non-2xx answer, normalised across SDKs. ``body`` is never logged."""

    def __init__(self, status: int, body: str = "", retry_after: float | None = None) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
        self.body = body
        self.retry_after = retry_after


class TransportError(Exception):
    """Timeout or connection failure below HTTP."""


def parse_retry_after(value: str | None) -> float | None:
    """Seconds from a ``retry-after`` header (only the numeric form is used)."""
    if not value:
        return None
    try:
        return max(0.0, min(MAX_RETRY_AFTER_S, float(value)))
    except ValueError:
        return None


def backoff_delay(attempt: int, jitter: Callable[[], float] = random.random) -> float:
    """Exponential backoff with jitter: about 1 s, 2 s, 4 s ... capped."""
    return min(MAX_BACKOFF_S, 2.0**attempt) * (0.5 + jitter())


@dataclass
class CircuitBreaker:
    """Opens after ``threshold`` consecutive failures; lets one trial call through after
    ``reset_after_s`` (half-open); closes again on success."""

    threshold: int = 3
    reset_after_s: float = 60.0
    clock: Callable[[], float] = time.monotonic
    failures: int = 0
    opened_at: float | None = None

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        if self.clock() - self.opened_at >= self.reset_after_s:
            return "half_open"
        return "open"

    def allow(self) -> bool:
        return self.state != "open"

    def success(self) -> None:
        with self._lock:
            self.failures, self.opened_at = 0, None

    def failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.failures >= self.threshold or self.opened_at is not None:
                self.opened_at = self.clock()


def classify(provider: str, error: HttpStatusError) -> ProviderError:
    """Map an HTTP failure to the error type the chain acts on."""
    status, body = error.status, error.body.lower()
    if status == 402 or (status in (400, 403, 429) and any(w in body for w in CREDIT_WORDS)):
        return CreditsExhaustedError(
            "The AI account has no credit left. AI is temporarily off (template mode).",
            provider, status,
        )
    if status in (401, 403):
        return ProviderAuthError(
            "The AI service rejected the key. AI is temporarily off (template mode).",
            provider, status,
        )
    if status == 429 or status >= 500:
        return ProviderTransientError(
            "The AI service is busy or not reachable. Please try again in a moment.",
            provider, status,
        )
    return ProviderRejectedError("The AI service rejected the request.", provider, status)


class HttpProviderBase:
    """Retry, circuit and classification around a provider-specific ``_send``."""

    name = "http"
    external = True
    capabilities = Capabilities()

    def __init__(
        self,
        *,
        models: dict[Tier, str],
        api_key: str,
        api_base: str,
        timeout_s: float = 25.0,
        max_retries: int = 2,
        breaker: CircuitBreaker | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.models = {tier: name.strip() for tier, name in models.items() if name.strip()}
        self.model = self.models.get("main") or next(iter(self.models.values()), "")
        self.api_base = api_base.rstrip("/")
        self._api_key = api_key.strip()
        self.timeout_s = timeout_s
        self.max_retries = max(0, max_retries)
        self.breaker = breaker or CircuitBreaker()
        self._sleep = sleep

    def __repr__(self) -> str:  # never shows the key
        return f"{type(self).__name__}(name={self.name!r}, model={self.model!r})"

    def model_for(self, tier: Tier) -> str:
        """The configured model of a tier, falling back to ``main``."""
        return self.models.get(tier) or self.model

    # ---------------------------------------------------------------- to implement

    def _send(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        tool: ToolSpec | None,
        max_tokens: int,
    ) -> LLMResponse:
        raise NotImplementedError

    def _send_stream(
        self, model: str, messages: list[dict[str, str]], temperature: float, max_tokens: int
    ) -> Iterator[str]:
        raise NotImplementedError

    def list_models(self) -> list[str]:
        """Model ids the account can use (used to check configured names at startup)."""
        raise NotImplementedError

    # ---------------------------------------------------------------- shared logic

    def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        *,
        tool: ToolSpec | None = None,
        tier: Tier = "main",
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """One answer with retries; raises a ``ProviderError`` subclass."""
        model = self.model_for(tier)
        response = self._with_retries(
            lambda: self._send(model, messages, temperature, tool, max_tokens or 1024)
        )
        response.provider, response.model = self.name, response.model or model
        return response

    def stream(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        *,
        tier: Tier = "main",
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        """Text chunks. Retries happen only before the first chunk arrives."""
        model = self.model_for(tier)
        iterator = self._with_retries(
            lambda: _primed(self._send_stream(model, messages, temperature, max_tokens or 1024))
        )
        try:
            yield from iterator
        except (HttpStatusError, TransportError) as exc:
            self.breaker.failure()
            raise self._final_error(exc) from None
        self.breaker.success()

    def _with_retries(self, call: Callable[[], Any]) -> Any:
        if not self.breaker.allow():
            raise CircuitOpenError(
                "The AI service failed several times; it is paused for a minute.", self.name
            )
        for attempt in range(self.max_retries + 1):
            try:
                result = call()
            except HttpStatusError as exc:
                error = classify(self.name, exc)
                retry = isinstance(error, ProviderTransientError) and attempt < self.max_retries
                logger.warning(
                    "llm_call_failed provider=%s status=%s retry=%s", self.name, exc.status, retry
                )
                if retry:
                    self._sleep(exc.retry_after if exc.retry_after is not None
                                else backoff_delay(attempt))
                    continue
                if isinstance(error, ProviderTransientError):
                    self.breaker.failure()
                raise error from None
            except TransportError as exc:
                retry = attempt < self.max_retries
                logger.warning(
                    "llm_call_failed provider=%s type=%s retry=%s",
                    self.name, type(exc.__cause__ or exc).__name__, retry,
                )
                if retry:
                    self._sleep(backoff_delay(attempt))
                    continue
                self.breaker.failure()
                raise self._final_error(exc) from None
            else:
                self.breaker.success()
                return result
        raise ProviderTransientError("The AI service could not be reached.", self.name)

    def _final_error(self, exc: Exception) -> ProviderError:
        if isinstance(exc, HttpStatusError):
            return classify(self.name, exc)
        return ProviderTransientError(
            "The AI service is busy or not reachable. Please try again in a moment.", self.name
        )


def _primed(iterator: Iterator[str]) -> Iterator[str]:
    """Pull the first chunk now, so connection errors surface inside the retry loop."""
    first = next(iterator, None)

    def chain() -> Iterator[str]:
        if first is not None:
            yield first
        yield from iterator

    return chain()
