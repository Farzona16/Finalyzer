"""Shared provider types: capability flags, the provider protocol, responses and errors.

Logic elsewhere depends on ``Capabilities`` flags, never on a provider's name (spec 3.1).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from core.errors import LLMUnavailableError

Tier = Literal["fast", "main", "deep"]
TIERS: tuple[Tier, ...] = ("fast", "main", "deep")


@dataclass(frozen=True)
class Capabilities:
    """What a provider (and the model behind it) can do."""

    forced_tool: bool = False
    tool_loop: bool = False
    streaming: bool = False
    cache_markers: bool = False
    max_context: int = 0


@dataclass(frozen=True)
class ToolSpec:
    """One tool the model must call; ``schema`` is a JSON schema with an object root."""

    name: str
    description: str
    schema: dict[str, Any]


@dataclass
class LLMResponse:
    """Text (or forced-tool arguments) returned by a model plus token counts."""

    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    tool_args: dict[str, Any] | None = None
    provider: str = ""
    model: str = ""


@dataclass
class ProviderStatus:
    """What the UI and ``/health/ready`` show about one provider link."""

    name: str
    model: str
    state: str = "ok"  # ok | circuit_open | credits_exhausted | auth_failed | unknown_model
    detail: str = ""
    models_checked: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class LLMProvider(Protocol):
    """What the gateway needs from a provider."""

    name: str
    model: str
    api_base: str
    external: bool
    capabilities: Capabilities

    def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        *,
        tool: ToolSpec | None = None,
        tier: Tier = "main",
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Send chat messages; return text or the forced tool's arguments."""
        ...

    def stream(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        *,
        tier: Tier = "main",
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        """Yield text chunks as they arrive."""
        ...


class ProviderError(LLMUnavailableError):
    """A provider could not answer. The chain moves on to the next provider."""

    def __init__(self, message: str, provider: str = "", status: int | None = None) -> None:
        super().__init__(message)
        self.provider = provider
        self.status = status


class ProviderTransientError(ProviderError):
    """Timeout, connection problem, 429 or 5xx after all retries."""


class ProviderRejectedError(ProviderError):
    """A permanent 4xx that is not about credits or keys (bad request, unknown model)."""


class ProviderAuthError(ProviderError):
    """Key missing, invalid or not allowed (401/403). Disables the provider for the session."""


class CreditsExhaustedError(ProviderError):
    """The account has no credit left. Disables the provider for the session."""


class CircuitOpenError(ProviderError):
    """The circuit breaker is open; the provider is skipped until it cools down."""


class ForcedToolUnsupportedError(ProviderError):
    """Neither forced tools nor a JSON-schema response format were accepted."""


class BudgetExceededError(LLMUnavailableError):
    """A token budget is used up (hard stop): frames only."""

    def __init__(self, message: str, scope: str) -> None:
        super().__init__(message)
        self.scope = scope


DISABLING_ERRORS = (ProviderAuthError, CreditsExhaustedError)
