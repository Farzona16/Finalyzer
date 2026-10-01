"""LLM providers: the live one (LiteLLM) and the offline one (golden question lookup).

This is the only module that imports ``litellm``. Nothing here decides *whether* a call is
allowed; that is the job of the Egress Gate (``egress.py``).
"""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import yaml
from pydantic import BaseModel

from core.config import Settings
from core.errors import LLMUnavailableError

logger = logging.getLogger("finalyzer.llm")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GOLDEN_PATH = PROJECT_ROOT / "data" / "demo" / "golden_questions.yaml"
RETRYABLE_ERROR_NAMES = frozenset(
    {
        "RateLimitError", "Timeout", "APITimeoutError", "APIConnectionError",
        "InternalServerError", "ServiceUnavailableError", "BadGatewayError",
    }
)
OFFLINE_MESSAGE = (
    "Offline mode: I can only answer the built-in demo questions right now. "
    "To ask anything else, set LLM_MODEL and LLM_API_KEY (or use a local model) and restart."
)


@dataclass
class LLMResponse:
    """Text returned by a model plus token counts (0 when the provider did not report them)."""

    text: str
    tokens_in: int = 0
    tokens_out: int = 0


class Provider(Protocol):
    """What the Egress Gate needs from a model provider."""

    model: str
    api_base: str
    external: bool

    def complete(self, messages: list[dict[str, str]], temperature: float) -> LLMResponse:
        """Send chat messages and return the reply text."""
        ...


def is_retryable(exc: BaseException) -> bool:
    """Rate limits, timeouts, connection problems and 5xx errors are worth retrying."""
    if type(exc).__name__ in RETRYABLE_ERROR_NAMES:
        return True
    if isinstance(exc, TimeoutError | ConnectionError):
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and (status == 429 or status >= 500)


def backoff_delay(attempt: int, jitter: Callable[[], float] = random.random) -> float:
    """Exponential backoff with jitter: about 1s, 2s, 4s ... capped at 30s."""
    return min(30.0, 2.0**attempt) * (0.5 + jitter())


def call_with_retries(
    call: Callable[[], LLMResponse],
    max_retries: int,
    sleep: Callable[[float], None] = time.sleep,
) -> LLMResponse:
    """Run ``call``; retry transient failures with backoff; raise ``LLMUnavailableError``."""
    for attempt in range(max_retries + 1):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 - classified below
            retry = is_retryable(exc) and attempt < max_retries
            logger.warning("llm_call_failed type=%s retry=%s", type(exc).__name__, retry)
            if retry:
                sleep(backoff_delay(attempt))
                continue
            if is_retryable(exc):
                raise LLMUnavailableError(
                    "The AI service is busy or not reachable. Please try again in a moment."
                ) from exc
            raise LLMUnavailableError(
                "The AI service rejected the request. Check LLM_MODEL and LLM_API_KEY."
            ) from exc
    raise LLMUnavailableError("The AI service could not be reached.")  # pragma: no cover


class LiveProvider:
    """Calls a model through LiteLLM with timeouts and retries."""

    def __init__(self, settings: Settings, sleep: Callable[[float], None] = time.sleep) -> None:
        self.model = settings.llm_model
        self.api_base = settings.llm_api_base.strip()
        self.external = settings.privacy_mode != "local"
        self._api_key = settings.llm_api_key.strip()
        self._timeout_s = settings.llm_timeout_s
        self._max_retries = settings.llm_max_retries
        self._sleep = sleep

    def complete(self, messages: list[dict[str, str]], temperature: float) -> LLMResponse:
        """Send the messages; transient errors are retried with exponential backoff."""
        return call_with_retries(
            lambda: self._call_litellm(messages, temperature), self._max_retries, self._sleep
        )

    def _call_litellm(self, messages: list[dict[str, str]], temperature: float) -> LLMResponse:
        """One raw LiteLLM call (imported lazily: the import is slow and noisy)."""
        import litellm

        litellm.suppress_debug_info = True
        response = litellm.completion(
            model=self.model,
            messages=messages,
            temperature=temperature,
            timeout=self._timeout_s,
            api_key=self._api_key or None,
            api_base=self.api_base or None,
            num_retries=0,
        )
        text = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        return LLMResponse(
            text=text,
            tokens_in=int(getattr(usage, "prompt_tokens", 0) or 0),
            tokens_out=int(getattr(usage, "completion_tokens", 0) or 0),
        )


class GoldenQuestion(BaseModel):
    """A question with its known-good SQL, used by the offline mode and the evaluation."""

    id: str
    dataset: str
    question: str
    expected_sql: str


def normalize_question(text: str) -> str:
    """Lowercase, replace punctuation by spaces and collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower())).strip()


def load_golden_questions(path: Path = DEFAULT_GOLDEN_PATH) -> list[GoldenQuestion]:
    """Read the golden question file."""
    with open(path, encoding="utf-8") as handle:
        return [GoldenQuestion.model_validate(q) for q in yaml.safe_load(handle)["questions"]]


class OfflineProvider:
    """Answers only the golden demo questions, using their stored SQL. Sends nothing anywhere."""

    model = "offline"
    api_base = ""
    external = False

    def __init__(self, questions: list[GoldenQuestion] | None = None) -> None:
        self._questions = questions if questions is not None else load_golden_questions()
        self._index = {(q.dataset, normalize_question(q.question)): q for q in self._questions}

    def lookup(self, question: str, dataset: str | None) -> GoldenQuestion | None:
        """Find a golden question by its normalized text within one demo dataset."""
        if dataset is None:
            return None
        return self._index.get((dataset, normalize_question(question)))

    def suggestions(self, dataset: str | None, count: int = 6) -> list[str]:
        """Suggested questions for a demo dataset (spread over the whole list)."""
        pool = [q.question for q in self._questions if q.dataset == dataset]
        if len(pool) <= count:
            return pool
        step = len(pool) / count
        return [pool[int(i * step)] for i in range(count)]
