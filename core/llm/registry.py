"""Provider chain construction and process-wide provider state (spec 3.2, 3.3).

``LLMRuntime`` is shared by every gateway in a process (or a Streamlit session): it keeps the
circuit breakers, providers disabled for the session (credits exhausted, bad key), the model
check results and the token ledger. Providers are built once per settings fingerprint.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from core.config import Settings
from core.llm.base import DISABLING_ERRORS, ProviderError, ProviderStatus
from core.llm.budget import TokenLedger
from core.llm.http_base import CircuitBreaker

logger = logging.getLogger("finalyzer.llm")

DISABLED_STATES = ("credits_exhausted", "auth_failed", "unknown_model")


class LLMRuntime:
    """Shared, thread-safe provider state."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.ledger = TokenLedger()
        self._breakers: dict[str, CircuitBreaker] = {}
        self._status: dict[str, ProviderStatus] = {}
        self._cache_key: tuple[Any, ...] | None = None
        self._providers: list[Any] = []

    def breaker(self, name: str, settings: Settings) -> CircuitBreaker:
        with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(
                    threshold=max(1, settings.circuit_failures),
                    reset_after_s=float(settings.circuit_reset_s),
                )
            return self._breakers[name]

    def status(self, name: str, model: str = "") -> ProviderStatus:
        with self._lock:
            if name not in self._status:
                self._status[name] = ProviderStatus(name=name, model=model)
            return self._status[name]

    def statuses(self) -> list[ProviderStatus]:
        with self._lock:
            out = list(self._status.values())
        for item in out:
            breaker = self._breakers.get(item.name)
            if item.state == "ok" and breaker is not None and breaker.state == "open":
                item.state = "circuit_open"
            elif item.state == "circuit_open" and (breaker is None or breaker.state != "open"):
                item.state = "ok"
        return out

    def is_skipped(self, name: str) -> bool:
        with self._lock:
            status = self._status.get(name)
        return status is not None and status.state in DISABLED_STATES

    def record_failure(self, name: str, error: Exception) -> None:
        """Disable a provider for the session on credit or key errors."""
        if not isinstance(error, DISABLING_ERRORS):
            return
        state = "credits_exhausted" if "credit" in type(error).__name__.lower() else "auth_failed"
        status = self.status(name)
        status.state, status.detail = state, getattr(error, "message", str(error))
        logger.warning("llm_provider_disabled provider=%s state=%s", name, state)

    def enable_all(self) -> None:
        """Re-enable providers (admin action or a new process)."""
        with self._lock:
            for status in self._status.values():
                if status.state in ("credits_exhausted", "auth_failed"):
                    status.state, status.detail = "ok", ""

    def providers(self, settings: Settings) -> list[Any]:
        key = _fingerprint(settings)
        with self._lock:
            if key == self._cache_key:
                return list(self._providers)
        built = build_providers(settings, self)
        with self._lock:
            self._cache_key, self._providers = key, built
        return list(built)


def _fingerprint(settings: Settings) -> tuple[Any, ...]:
    return (
        tuple(settings.chain),
        settings.anthropic_api_key, settings.anthropic_base_url,
        tuple(sorted(settings.claude_models.items())),
        settings.xai_api_key, settings.openai_base_url, tuple(sorted(settings.grok_models.items())),
        settings.llm_api_key, settings.llm_api_base, settings.llm_model,
        settings.llm_timeout_s, settings.llm_max_retries, settings.egress_allowed_hosts,
    )


def build_providers(settings: Settings, runtime: LLMRuntime) -> list[Any]:
    """The configured links of ``LLM_PROVIDER_CHAIN`` in order (``offline`` is implicit)."""
    providers: list[Any] = []
    hosts = settings.allowed_hosts
    for name in settings.chain:
        if name == "offline" or not settings.provider_configured(name):
            continue
        common = {
            "timeout_s": float(settings.llm_timeout_s),
            "max_retries": settings.llm_max_retries,
            "breaker": runtime.breaker(name, settings),
        }
        try:
            if name == "anthropic":
                from core.llm.anthropic_provider import AnthropicProvider

                provider: Any = AnthropicProvider(
                    models=settings.claude_models, api_key=settings.anthropic_api_key,
                    api_base=settings.anthropic_base_url, allowed_hosts=hosts, **common,
                )
            elif name in ("grok", "custom"):
                from core.llm.openai_compat import OpenAICompatProvider

                grok = name == "grok"
                provider = OpenAICompatProvider(
                    name=name,
                    models=settings.grok_models if grok else {"main": settings.llm_model},
                    api_key=settings.xai_api_key if grok else settings.llm_api_key,
                    api_base=settings.openai_base_url if grok else settings.llm_api_base,
                    allowed_hosts=hosts, **common,
                )
            else:
                from core.llm.ollama import OllamaProvider

                provider = OllamaProvider(
                    models={"main": settings.llm_model}, api_base=settings.llm_api_base, **common
                )
        except ImportError:
            logger.warning("llm_provider_unavailable provider=%s reason=import", name)
            continue
        runtime.status(name, provider.model)
        providers.append(provider)
    return providers


def _same_model(configured: str, available: set[str]) -> bool:
    return configured in available or f"{configured}:latest" in available


def verify_models(providers: list[Any], runtime: LLMRuntime) -> dict[str, dict[str, Any]]:
    """Check every configured model name against the provider's model list (spec 3.2).

    An unknown name logs a warning and the provider is skipped in the chain. A provider whose
    list cannot be read (no network, bad key) stays unchecked; bad keys disable it.
    """
    report: dict[str, dict[str, Any]] = {}
    for provider in providers:
        status = runtime.status(provider.name, provider.model)
        try:
            available = set(provider.list_models())
        except ProviderError as exc:
            runtime.record_failure(provider.name, exc)
            report[provider.name] = {"checked": False, "error": type(exc).__name__}
            continue
        except Exception as exc:  # noqa: BLE001 - network failures must not stop startup
            from core.llm.http_base import HttpStatusError, classify

            if isinstance(exc, HttpStatusError):
                runtime.record_failure(provider.name, classify(provider.name, exc))
            logger.warning("llm_model_check_failed provider=%s type=%s",
                           provider.name, type(exc).__name__)
            report[provider.name] = {"checked": False, "error": type(exc).__name__}
            continue
        configured = dict(getattr(provider, "models", {}) or {"main": provider.model})
        unknown = sorted({m for m in configured.values() if not _same_model(m, available)})
        status.models_checked = True
        status.extra = {"available_count": len(available)}
        if unknown:
            status.state = "unknown_model"
            status.detail = f"not in the provider's model list: {', '.join(unknown)}"
            logger.warning("llm_unknown_model provider=%s models=%s", provider.name, unknown)
        report[provider.name] = {
            "checked": True, "unknown": unknown, "available": sorted(available),
        }
    return report
