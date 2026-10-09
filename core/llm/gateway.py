"""LLM gateway: the one place the rest of the app uses to talk to a model.

It walks the provider chain (``LLM_PROVIDER_CHAIN``, spec 3.3): every link is wrapped by its
own Egress Gate. When a circuit is open, a provider is out of credits, its key fails, its model
is unknown, it is not cleared for the data, or it errors, the request moves to the next link.
The last link is always offline: callers then use frames, the router and the golden answers.

JSON answers use a forced tool when the provider supports it (``capabilities.forced_tool``)
and are re-validated with Pydantic either way; invalid JSON is repaired once.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from core.config import Settings
from core.errors import LLMUnavailableError, PrivacyBlockedError
from core.llm.audit import AuditLog
from core.llm.base import (
    DISABLING_ERRORS,
    BudgetExceededError,
    LLMResponse,
    ProviderStatus,
    Tier,
    ToolSpec,
)
from core.llm.budget import budget_summary
from core.llm.egress import (
    DataOrigin,
    EgressContext,
    EgressGate,
    ProviderNotClearedError,
    default_origin,
)
from core.llm.providers import OfflineProvider
from core.llm.registry import LLMRuntime

logger = logging.getLogger("finalyzer.llm")

FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_CACHE_LOCK = threading.Lock()
_PROMPT_CACHE: dict[str, LLMResponse] = {}
_PROMPT_CACHE_ORDER: list[str] = []
_PROMPT_CACHE_MAX = 64
TEMPLATE_MODE_MESSAGE = "AI is temporarily off; template mode."

# Task -> (model tier, max output tokens). Unknown purposes use ("main", 800).
TASKS: dict[str, tuple[Tier, int]] = {
    "nl2sql": ("main", 700),
    "narrate": ("main", 1200),
    "analyst": ("main", 900),
    "analyst_deep": ("deep", 1500),
    "polish": ("fast", 400),
    "suggest": ("fast", 900),
    "domain": ("fast", 150),
    "smoke": ("fast", 64),
}


def task_of(purpose: str) -> tuple[Tier, int]:
    base = purpose.removesuffix("_repair")
    return TASKS.get(base, ("main", 800))


def _prompt_key(purpose: str, messages: list[dict[str, str]], provider: str = "",
                model: str = "", tool: str = "") -> str:
    blob = json.dumps(
        {"p": purpose, "m": messages, "prov": provider, "model": model, "tool": tool},
        ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def _cache_get(key: str) -> LLMResponse | None:
    with _CACHE_LOCK:
        return _PROMPT_CACHE.get(key)


def _cache_put(key: str, response: LLMResponse) -> None:
    with _CACHE_LOCK:
        if key in _PROMPT_CACHE:
            _PROMPT_CACHE[key] = response
            return
        if len(_PROMPT_CACHE_ORDER) >= _PROMPT_CACHE_MAX:
            old = _PROMPT_CACHE_ORDER.pop(0)
            _PROMPT_CACHE.pop(old, None)
        _PROMPT_CACHE_ORDER.append(key)
        _PROMPT_CACHE[key] = response


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


def tool_for(purpose: str, schema: type[BaseModel]) -> ToolSpec:
    """The forced tool that carries a JSON answer of ``schema``."""
    name = re.sub(r"[^A-Za-z0-9_-]", "_", f"submit_{purpose.removesuffix('_repair')}")[:64]
    return ToolSpec(
        name=name,
        description=f"Return the {purpose.removesuffix('_repair')} answer as structured data.",
        schema=schema.model_json_schema(),
    )


@dataclass
class _Link:
    provider: Any
    gate: EgressGate


class LLMGateway:
    """Session-level access to the provider chain (or to the offline lookup)."""

    def __init__(
        self,
        settings: Settings,
        audit: AuditLog | None = None,
        provider: Any | None = None,
        offline: OfflineProvider | None = None,
        *,
        providers: list[Any] | None = None,
        runtime: LLMRuntime | None = None,
        data_origin: DataOrigin | None = None,
        dataset_id: str | None = None,
        kill_switch: bool | None = None,
    ) -> None:
        self.settings = settings
        self.audit = audit or AuditLog()
        self.offline = offline or OfflineProvider()
        self.runtime = runtime or LLMRuntime()
        self.dataset_id = dataset_id
        if providers is None:
            if provider is not None:
                providers = [provider]
            elif settings.llm_mode != "offline":
                providers = build_providers(settings, self.runtime)
            else:
                providers = []
        if kill_switch is None:
            kill_switch = settings.kill_switch_default == "on"
        context = EgressContext(
            data_origin=data_origin or default_origin(settings),
            dataset_id=dataset_id,
            kill_switch=kill_switch,
            ledger=self.runtime.ledger,
        )
        self.kill_switch = kill_switch
        self._links = [_Link(p, EgressGate(settings, self.audit, p, context)) for p in providers]

    # ------------------------------------------------------------------ state

    @property
    def provider(self) -> Any | None:
        """The first usable provider (``None`` in offline mode)."""
        links = self._usable()
        return links[0].provider if links else None

    @property
    def is_offline(self) -> bool:
        """True when no provider can be used; only frames and golden answers remain."""
        return not self._usable()

    @property
    def primary_model(self) -> str:
        provider = self.provider
        return str(getattr(provider, "model", "")) if provider is not None else "offline"

    @property
    def has_deep(self) -> bool:
        """The Deep analysis button is shown only when ``deep`` differs from ``main``."""
        provider = self.provider
        models = getattr(provider, "models", None) if provider is not None else None
        return bool(models) and models.get("deep", "") not in ("", models.get("main", ""))

    def provider_names(self) -> list[str]:
        return [str(getattr(link.provider, "name", "")) for link in self._usable()]

    def status(self) -> dict[str, Any]:
        """Provider badges for the UI: per-link state plus whether template mode is on."""
        rows: list[ProviderStatus] = []
        known = {s.name: s for s in self.runtime.statuses()}
        for link in self._links:
            name = str(getattr(link.provider, "name", "") or "provider")
            rows.append(known.get(name) or ProviderStatus(name=name, model=link.provider.model))
        return {
            "template_mode": self.is_offline,
            "kill_switch": self.kill_switch,
            "providers": [row.__dict__ for row in rows],
            "has_deep": self.has_deep,
            "budgets": budget_summary(
                self.settings, self.runtime.ledger, self.audit.tokens_used, self.dataset_id
            ),
        }

    def _usable(self) -> list[_Link]:
        return [
            link for link in self._links
            if not self.runtime.is_skipped(str(getattr(link.provider, "name", "")))
        ]

    # ------------------------------------------------------------------ calls

    def _attempt(
        self,
        purpose: str,
        messages: list[dict[str, str]],
        temperature: float,
        tool: ToolSpec | None,
        exclude: set[str] | None = None,
    ) -> LLMResponse:
        """Walk the chain; return the first answer. Raises when every link fails."""
        if not self._links:
            raise LLMUnavailableError("No AI model is configured (offline mode).")
        tier, max_tokens = task_of(purpose)
        failures: list[Exception] = []
        for link in self._usable():
            name = str(getattr(link.provider, "name", ""))
            if exclude and name in exclude:
                continue
            capabilities = getattr(link.provider, "capabilities", None)
            use_tool = tool if (tool is not None and capabilities and capabilities.forced_tool) \
                else None
            key = ""
            if self.settings.cache_identical_prompts:
                key = _prompt_key(purpose, messages, name, link.provider.model,
                                  use_tool.name if use_tool else "")
                hit = _cache_get(key)
                if hit is not None:
                    return hit
            try:
                response = link.gate.send(
                    purpose, messages, temperature, tool=use_tool, tier=tier,
                    max_tokens=max_tokens,
                )
            except BudgetExceededError:
                raise
            except ProviderNotClearedError as exc:
                failures.append(exc)
                continue
            except PrivacyBlockedError:
                raise  # a payload problem: no other provider may receive it either
            except Exception as exc:  # noqa: BLE001 - every provider failure falls through
                if isinstance(exc, DISABLING_ERRORS):
                    self.runtime.record_failure(name, exc)
                logger.warning("llm_chain_fallthrough provider=%s type=%s",
                               name or "provider", type(exc).__name__)
                failures.append(exc)
                continue
            response.provider = response.provider or name
            response.model = response.model or link.provider.model
            if key:
                _cache_put(key, response)
            return response
        raise self._exhausted(failures)

    @staticmethod
    def _exhausted(failures: list[Exception]) -> Exception:
        if failures and all(isinstance(f, ProviderNotClearedError) for f in failures):
            return failures[0]
        last = next((f for f in reversed(failures) if isinstance(f, LLMUnavailableError)), None)
        if last is not None:
            if "template mode" in last.message.lower():
                return last
            return LLMUnavailableError(f"{last.message} {TEMPLATE_MODE_MESSAGE}")
        return LLMUnavailableError(
            f"The AI service could not answer. {TEMPLATE_MODE_MESSAGE}"
        )

    def complete_text(
        self,
        purpose: str,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        *,
        exclude: set[str] | None = None,
    ) -> str:
        """Send messages through the chain and return the raw reply text."""
        return self._attempt(purpose, messages, temperature, None, exclude).text

    def complete_json[T: BaseModel](
        self,
        purpose: str,
        messages: list[dict[str, str]],
        schema: type[T],
        temperature: float = 0.0,
        *,
        exclude: set[str] | None = None,
    ) -> T:
        """A validated JSON answer; on an unusable reply, ask once for a corrected one."""
        value, _ = self.complete_json_meta(purpose, messages, schema, temperature, exclude=exclude)
        return value

    def complete_json_meta[T: BaseModel](
        self,
        purpose: str,
        messages: list[dict[str, str]],
        schema: type[T],
        temperature: float = 0.0,
        *,
        exclude: set[str] | None = None,
    ) -> tuple[T, LLMResponse]:
        """Like ``complete_json`` but also returns the response (provider, model, tokens)."""
        tool = tool_for(purpose, schema)
        response = self._attempt(purpose, messages, temperature, tool, exclude)
        try:
            return self._validated(response, schema), response
        except ValueError as exc:
            reply = response.text or json.dumps(response.tool_args or {})
            repair = messages + [
                {"role": "assistant", "content": reply},
                {
                    "role": "user",
                    "content": f"That reply could not be used ({str(exc)[:300]}). "
                    "Return only the corrected JSON object.",
                },
            ]
        second = self._attempt(f"{purpose}_repair", repair, temperature, tool, exclude)
        try:
            return self._validated(second, schema), second
        except ValueError as exc:
            raise LLMUnavailableError(
                "The AI returned an answer in an unusable format. Please try again."
            ) from exc

    @staticmethod
    def _validated[T: BaseModel](response: LLMResponse, schema: type[T]) -> T:
        if response.tool_args is not None:
            try:
                return schema.model_validate(response.tool_args)
            except PydanticValidationError as exc:
                raise ValueError(f"invalid tool arguments: {exc.error_count()} problems") from exc
        return parse_json_reply(response.text, schema)

    def stream_text(
        self, purpose: str, messages: list[dict[str, str]], temperature: float = 0.0
    ) -> Iterator[str]:
        """Text chunks from the first streaming-capable link; falls back to one chunk."""
        tier, max_tokens = task_of(purpose)
        for link in self._usable():
            capabilities = getattr(link.provider, "capabilities", None)
            if not (capabilities and capabilities.streaming):
                continue
            name = str(getattr(link.provider, "name", ""))
            started = False
            try:
                for chunk in link.gate.stream(
                    purpose, messages, temperature, tier=tier, max_tokens=max_tokens
                ):
                    started = True
                    yield chunk
                return
            except (BudgetExceededError, PrivacyBlockedError) as exc:
                if isinstance(exc, ProviderNotClearedError) and not started:
                    continue
                raise
            except Exception as exc:  # noqa: BLE001
                if started:
                    raise
                if isinstance(exc, DISABLING_ERRORS):
                    self.runtime.record_failure(name, exc)
                logger.warning("llm_stream_fallthrough provider=%s type=%s",
                               name, type(exc).__name__)
        yield self.complete_text(purpose, messages, temperature)


def build_providers(settings: Settings, runtime: LLMRuntime) -> list[Any]:
    """Providers of the chain (patched in tests to inject fakes)."""
    return runtime.providers(settings)
