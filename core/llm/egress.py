"""The Egress Gate: the only way to reach an LLM (Section 3, principle 4).

``EgressGate.send`` runs these steps in order:
1. kill switch (no external call while it is on),
2. host allowlist (``EGRESS_ALLOWED_HOSTS``) and local-mode endpoint check,
3. provider clearance (real data needs an admin-confirmed clearance for that provider),
4. payload scan (email, card number, phone number),
5. size and token-budget check (session, dataset, day),
6. write the audit record with the exact payload text (before sending),
7. send,
8. update tokens and latency.
Blocked calls are audited too. Blocked payloads are stored with the sensitive parts redacted.
"""

from __future__ import annotations

import copy
import ipaddress
import json
import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlparse

from core.config import Settings
from core.errors import PrivacyBlockedError
from core.ingest.pii import luhn_valid
from core.llm.audit import AuditLog
from core.llm.base import BudgetExceededError, LLMResponse, Tier, ToolSpec
from core.llm.budget import TokenLedger, over_budget
from core.models import AuditEvent

logger = logging.getLogger("finalyzer.llm")

MAX_PAYLOAD_BYTES = 200_000
CHARS_PER_TOKEN = 4
DataOrigin = Literal["synthetic", "real"]
LEVEL_RANK = {"strict": 1, "balanced": 2, "open": 3}
CLEARANCE_RANK = {"synthetic_only": 0, "strict_ok": 1, "balanced_ok": 2}

# The look-behind keeps the scan linear: a match may only start at the beginning of a word run.
EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+\.[\w.-]+")
CARD_PATTERNS = (
    re.compile(r"(?<![\d.])\d{13,19}(?![\d.])"),
    re.compile(r"(?<!\d)\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{1,7}(?!\d)"),
    re.compile(r"(?<!\d)\d{4}[ -]\d{6}[ -]\d{5}(?!\d)"),
)
PHONE_PATTERNS = (
    re.compile(r"\+\d[\d\s().-]{6,17}\d"),
    re.compile(r"\(\d{3}\)\s?\d{3}[-.\s]?\d{4}"),
    re.compile(r"(?<!\d)\d{3}[-.\s]\d{3}[-.\s]\d{4}(?!\d)"),
)


class ProviderNotClearedError(PrivacyBlockedError):
    """This provider may not receive this data; another provider (or offline) may."""


def serialize_payload(messages: list[dict[str, str]]) -> str:
    """The exact text that is audited and sent (the JSON form of the chat messages)."""
    return json.dumps(messages, ensure_ascii=False)


def estimate_tokens(text: str) -> int:
    """Rough token count used for the budget when the provider reports none."""
    return max(1, len(text) // CHARS_PER_TOKEN)


def host_of(api_base: str) -> str:
    """Lower-case host name of a base URL ('' when there is none)."""
    if not api_base.strip():
        return ""
    url = api_base if "://" in api_base else f"http://{api_base}"
    return (urlparse(url).hostname or "").lower()


def is_private_endpoint(api_base: str) -> bool:
    """True for localhost and loopback/private IP addresses. Other host names are not trusted."""
    host = host_of(api_base)
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


def find_sensitive(text: str) -> list[tuple[str, str]]:
    """Return ``(kind, matched text)`` for every email, card number and phone number found."""
    found: list[tuple[str, str]] = [("email", m.group()) for m in EMAIL_RE.finditer(text)]
    for pattern in CARD_PATTERNS:
        for match in pattern.finditer(text):
            digits = re.sub(r"\D", "", match.group())
            if 13 <= len(digits) <= 19 and luhn_valid(digits):
                found.append(("card number", match.group()))
    for pattern in PHONE_PATTERNS:
        found += [("phone number", m.group()) for m in pattern.finditer(text)]
    return found


def redact(text: str, findings: list[tuple[str, str]]) -> str:
    """Replace the sensitive matches so that blocked payloads can be logged safely."""
    for kind, value in sorted(findings, key=lambda f: -len(f[1])):
        text = text.replace(value, f"[REDACTED {kind}]")
    return text


@dataclass
class EgressContext:
    """Per-call facts the gate needs besides the settings."""

    data_origin: DataOrigin = "real"
    dataset_id: str | None = None
    kill_switch: bool = False
    ledger: TokenLedger = field(default_factory=TokenLedger)


def default_origin(settings: Settings) -> DataOrigin:
    """Demo privacy mode only ever handles the synthetic demo datasets."""
    return "synthetic" if settings.privacy_mode == "demo" else "real"


def clearance_allows(settings: Settings, provider_name: str, origin: DataOrigin) -> bool:
    """Synthetic data may go anywhere allowed; real data needs a clearance >= the level."""
    if origin == "synthetic":
        return True
    granted = CLEARANCE_RANK.get(settings.clearance.get(provider_name, "synthetic_only"), 0)
    return granted >= LEVEL_RANK[settings.privacy_level]


class EgressGate:
    """Checks, audits and sends every LLM call to one provider."""

    def __init__(
        self,
        settings: Settings,
        audit: AuditLog,
        provider,  # noqa: ANN001 - any object with the provider attributes (fakes in tests)
        context: EgressContext | None = None,
    ) -> None:
        self._settings = settings
        self._audit = audit
        self._provider = provider
        self._context = context or EgressContext(data_origin=default_origin(settings))

    @property
    def external(self) -> bool:
        """Whether a call leaves the private network."""
        if self._settings.privacy_mode == "local":
            return False
        if getattr(self._provider, "external", True) and is_private_endpoint(
            self._provider.api_base
        ):
            return False
        return bool(getattr(self._provider, "external", True))

    @property
    def provider_name(self) -> str:
        return str(getattr(self._provider, "name", "") or "")

    def send(
        self,
        purpose: str,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        *,
        tool: ToolSpec | None = None,
        tier: Tier = "main",
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Send chat messages to the model after all checks. Raises when blocked."""
        event = self._checked_event(purpose, messages)
        return self._deliver(event, messages, temperature, tool, tier, max_tokens)

    def stream(
        self,
        purpose: str,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        *,
        tier: Tier = "main",
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        """Like ``send`` but yields text chunks; tokens are counted when the stream ends."""
        event = self._checked_event(purpose, messages)
        started = time.perf_counter()
        parts: list[str] = []
        try:
            for chunk in self._provider.stream(
                copy.deepcopy(messages), temperature, tier=tier, max_tokens=max_tokens
            ):
                parts.append(chunk)
                yield chunk
        except Exception as exc:
            event.error = type(exc).__name__
            raise
        finally:
            event.latency_ms = round((time.perf_counter() - started) * 1000, 1)
            event.tokens_out = estimate_tokens("".join(parts)) if parts else 0
            self._context.ledger.add(event.tokens_in + event.tokens_out, self._context.dataset_id)

    # ------------------------------------------------------------------ checks

    def _checked_event(self, purpose: str, messages: list[dict[str, str]]) -> AuditEvent:
        payload_text = serialize_payload(messages)
        model = getattr(self._provider, "model", "")
        name = self.provider_name
        event = AuditEvent(
            ts=datetime.now(UTC).isoformat(timespec="milliseconds"),
            purpose=purpose,
            mode=self._settings.privacy_mode,
            provider_model=f"{name}:{model}" if name else model,
            payload_text=payload_text,
            payload_bytes=len(payload_text.encode("utf-8")),
            external=self.external,
        )
        content = "\n".join(m.get("content", "") for m in messages)
        self._check_kill_switch(event)
        self._check_mode(event)
        self._check_host(event)
        self._check_clearance(event)
        self._check_size(event)
        self._check_payload(event, content)
        self._check_budget(event)
        self._audit.add(event)
        return event

    def _block(self, event: AuditEvent, reason: str, error: type[Exception], message: str) -> None:
        """Record a blocked call and raise the matching error."""
        event.blocked, event.block_reason = True, reason
        self._audit.add(event)
        if error is BudgetExceededError:
            raise BudgetExceededError(message, reason.split()[0])
        raise error(message)

    def _check_kill_switch(self, event: AuditEvent) -> None:
        if self._context.kill_switch and event.external:
            self._block(
                event,
                "kill switch is on",
                ProviderNotClearedError,
                "Blocked: the administrator turned off all external AI calls (kill switch).",
            )

    def _check_mode(self, event: AuditEvent) -> None:
        if self._settings.privacy_mode == "local" and not is_private_endpoint(
            self._provider.api_base
        ):
            self._block(
                event,
                "local mode allows only loopback or private model endpoints",
                PrivacyBlockedError,
                "Blocked: local privacy mode only allows a model server on this computer or "
                "private network.",
            )

    def _check_host(self, event: AuditEvent) -> None:
        host = host_of(self._provider.api_base)
        if not host or is_private_endpoint(self._provider.api_base):
            return  # in-process providers (tests) and local model servers
        if host not in self._settings.allowed_hosts:
            self._block(
                event,
                f"host {host} is not in EGRESS_ALLOWED_HOSTS",
                ProviderNotClearedError,
                "Blocked: this AI service is not on the list of allowed hosts.",
            )

    def _check_clearance(self, event: AuditEvent) -> None:
        if not event.external:
            return
        name = self.provider_name or "unknown"
        if not clearance_allows(self._settings, name, self._context.data_origin):
            self._block(
                event,
                f"provider {name} is not cleared for real data at level "
                f"{self._settings.privacy_level}",
                ProviderNotClearedError,
                "Blocked: this AI provider is only cleared for demo (synthetic) data. An admin "
                "can change PROVIDER_CLEARANCE after confirming the provider's data terms.",
            )

    def _check_payload(self, event: AuditEvent, content: str) -> None:
        findings = find_sensitive(content)
        if findings:
            kinds = sorted({kind for kind, _ in findings})
            event.payload_text = redact(event.payload_text, findings)
            self._block(
                event,
                f"payload contains {', '.join(kinds)}",
                PrivacyBlockedError,
                "Blocked: the request contained personal data "
                f"({', '.join(kinds)}), so nothing was sent.",
            )

    def _check_size(self, event: AuditEvent) -> None:
        if event.payload_bytes > MAX_PAYLOAD_BYTES:
            self._block(
                event,
                f"payload larger than {MAX_PAYLOAD_BYTES} bytes",
                PrivacyBlockedError,
                "Blocked: the request was unusually large, so nothing was sent.",
            )

    def _check_budget(self, event: AuditEvent) -> None:
        estimate = estimate_tokens(event.payload_text)
        scope = over_budget(
            self._settings,
            self._context.ledger,
            self._audit.tokens_used,
            estimate,
            self._context.dataset_id,
        )
        if scope is not None:
            if self._settings.hard_stop_on_budget:
                self._block(
                    event,
                    f"{scope} token budget exceeded",
                    BudgetExceededError,
                    f"The AI token budget ({scope}) is used up. Everything else keeps working "
                    "with template text.",
                )
            logger.warning("token_budget_exceeded scope=%s hard_stop=false", scope)
        event.tokens_in = estimate

    def _deliver(
        self,
        event: AuditEvent,
        messages: list[dict[str, str]],
        temperature: float,
        tool: ToolSpec | None,
        tier: Tier,
        max_tokens: int | None,
    ) -> LLMResponse:
        started = time.perf_counter()
        kwargs: dict[str, object] = {}
        if tool is not None:
            kwargs["tool"] = tool
        if hasattr(self._provider, "capabilities"):
            kwargs.update(tier=tier, max_tokens=max_tokens)
        try:
            response = self._provider.complete(copy.deepcopy(messages), temperature, **kwargs)
        except Exception as exc:
            event.error = type(exc).__name__
            event.latency_ms = round((time.perf_counter() - started) * 1000, 1)
            raise
        event.latency_ms = round((time.perf_counter() - started) * 1000, 1)
        event.tokens_in = response.tokens_in or event.tokens_in
        out_text = response.text or (json.dumps(response.tool_args) if response.tool_args else "")
        event.tokens_out = response.tokens_out or estimate_tokens(out_text)
        if response.model and self.provider_name:
            event.provider_model = f"{self.provider_name}:{response.model}"
        self._context.ledger.add(event.tokens_in + event.tokens_out, self._context.dataset_id)
        return response
