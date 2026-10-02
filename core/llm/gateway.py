"""LLM gateway: the one place the rest of the app uses to talk to a model.

It picks the provider from the settings (live or offline), sends every request through the
Egress Gate, parses JSON replies with Pydantic and repairs invalid JSON once.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading

from pydantic import BaseModel

from core.config import Settings
from core.errors import LLMUnavailableError
from core.llm.audit import AuditLog
from core.llm.egress import EgressGate
from core.llm.providers import LiveProvider, OfflineProvider, Provider

FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_CACHE_LOCK = threading.Lock()
_PROMPT_CACHE: dict[str, str] = {}
_PROMPT_CACHE_ORDER: list[str] = []
_PROMPT_CACHE_MAX = 64


def _prompt_key(purpose: str, messages: list[dict[str, str]]) -> str:
    blob = json.dumps({"p": purpose, "m": messages}, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def _cache_get(key: str) -> str | None:
    with _CACHE_LOCK:
        return _PROMPT_CACHE.get(key)


def _cache_put(key: str, text: str) -> None:
    with _CACHE_LOCK:
        if key in _PROMPT_CACHE:
            _PROMPT_CACHE[key] = text
            return
        if len(_PROMPT_CACHE_ORDER) >= _PROMPT_CACHE_MAX:
            old = _PROMPT_CACHE_ORDER.pop(0)
            _PROMPT_CACHE.pop(old, None)
        _PROMPT_CACHE_ORDER.append(key)
        _PROMPT_CACHE[key] = text


def clear_prompt_cache() -> None:
    """Drop the process-wide identical-prompt cache (tests)."""
    with _CACHE_LOCK:
        _PROMPT_CACHE.clear()
        _PROMPT_CACHE_ORDER.clear()


def parse_json_reply[T: BaseModel](text: str, schema: type[T]) -> T:
    """Parse a model reply into ``schema``. Raises ``ValueError`` when it does not fit."""
    cleaned = FENCE_RE.sub("", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("the reply contains no JSON object")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    return schema.model_validate(data)


class LLMGateway:
    """Session-level access to the model (or to the offline lookup)."""

    def __init__(
        self,
        settings: Settings,
        audit: AuditLog | None = None,
        provider: Provider | None = None,
        offline: OfflineProvider | None = None,
    ) -> None:
        self.settings = settings
        self.audit = audit or AuditLog()
        self.offline = offline or OfflineProvider()
        if provider is None and settings.effective_llm_mode == "live":
            provider = LiveProvider(settings)
        self.provider = provider
        self._gate = EgressGate(settings, self.audit, provider) if provider else None

    @property
    def is_offline(self) -> bool:
        """True when no model is available; only the golden questions can be answered."""
        return self._gate is None

    def complete_text(
        self, purpose: str, messages: list[dict[str, str]], temperature: float = 0.0
    ) -> str:
        """Send messages through the Egress Gate and return the raw reply text."""
        if self._gate is None:
            raise LLMUnavailableError("No AI model is configured (offline mode).")
        if self.settings.cache_identical_prompts:
            key = _prompt_key(purpose, messages)
            hit = _cache_get(key)
            if hit is not None:
                return hit
            text = self._gate.send(purpose, messages, temperature).text
            _cache_put(key, text)
            return text
        return self._gate.send(purpose, messages, temperature).text

    def complete_json[T: BaseModel](
        self,
        purpose: str,
        messages: list[dict[str, str]],
        schema: type[T],
        temperature: float = 0.0,
    ) -> T:
        """Ask for a JSON reply; on invalid JSON, ask once for a corrected reply."""
        reply = self.complete_text(purpose, messages, temperature)
        try:
            return parse_json_reply(reply, schema)
        except ValueError as exc:
            repair = messages + [
                {"role": "assistant", "content": reply},
                {
                    "role": "user",
                    "content": f"That reply could not be used ({str(exc)[:300]}). "
                    "Return only the corrected JSON object.",
                },
            ]
        second = self.complete_text(f"{purpose}_repair", repair, temperature)
        try:
            return parse_json_reply(second, schema)
        except ValueError as exc:
            raise LLMUnavailableError(
                "The AI returned an answer in an unusable format. Please try again."
            ) from exc
