"""The Egress Gate: the only way to reach an LLM (Section 3, principle 4).

``EgressGate.send`` runs these steps in order:
1. mode check (local mode may only talk to loopback/private addresses),
2. payload scan (email, card number, phone number),
3. size and token-budget check,
4. write the audit record with the exact payload text (before sending),
5. send,
6. update tokens and latency.
Blocked calls are audited too. Blocked payloads are stored with the sensitive parts redacted.
"""

from __future__ import annotations

import copy
import ipaddress
import json
import re
import time
from datetime import UTC, datetime
from urllib.parse import urlparse

from core.config import Settings
from core.errors import LLMUnavailableError, PrivacyBlockedError
from core.ingest.pii import luhn_valid
from core.llm.audit import AuditLog
from core.llm.providers import LLMResponse, Provider
from core.models import AuditEvent

MAX_PAYLOAD_BYTES = 200_000
CHARS_PER_TOKEN = 4

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


def serialize_payload(messages: list[dict[str, str]]) -> str:
    """The exact text that is audited and sent (the JSON form of the chat messages)."""
    return json.dumps(messages, ensure_ascii=False)


def estimate_tokens(text: str) -> int:
    """Rough token count used for the budget when the provider reports none."""
    return max(1, len(text) // CHARS_PER_TOKEN)


def is_private_endpoint(api_base: str) -> bool:
    """True for localhost and loopback/private IP addresses. Other host names are not trusted."""
    if not api_base.strip():
        return False
    url = api_base if "://" in api_base else f"http://{api_base}"
    host = urlparse(url).hostname
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


class EgressGate:
    """Checks, audits and sends every LLM call of a session."""

    def __init__(self, settings: Settings, audit: AuditLog, provider: Provider) -> None:
        self._settings = settings
        self._audit = audit
        self._provider = provider

    @property
    def external(self) -> bool:
        """Whether a call leaves the private network."""
        return bool(self._provider.external and self._settings.privacy_mode != "local")

    def send(
        self, purpose: str, messages: list[dict[str, str]], temperature: float = 0.0
    ) -> LLMResponse:
        """Send chat messages to the model after all checks. Raises when blocked."""
        payload_text = serialize_payload(messages)
        payload_bytes = len(payload_text.encode("utf-8"))
        event = AuditEvent(
            ts=datetime.now(UTC).isoformat(timespec="milliseconds"),
            purpose=purpose,
            mode=self._settings.privacy_mode,
            provider_model=self._provider.model,
            payload_text=payload_text,
            payload_bytes=payload_bytes,
            external=self.external,
        )
        content = "\n".join(m.get("content", "") for m in messages)
        self._check_mode(event)
        self._check_size(event)
        self._check_payload(event, content)
        self._check_budget(event)
        self._audit.add(event)
        return self._deliver(event, messages, temperature)

    def _block(self, event: AuditEvent, reason: str, error: type[Exception], message: str) -> None:
        """Record a blocked call and raise the matching error."""
        event.blocked, event.block_reason = True, reason
        self._audit.add(event)
        raise error(message)

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
        if self._audit.tokens_used + estimate > self._settings.session_token_budget:
            self._block(
                event,
                "session token budget exceeded",
                LLMUnavailableError,
                "The AI token budget for this session is used up. Everything else keeps "
                "working; clear the session to reset it.",
            )
        event.tokens_in = estimate

    def _deliver(
        self, event: AuditEvent, messages: list[dict[str, str]], temperature: float
    ) -> LLMResponse:
        started = time.perf_counter()
        try:
            response = self._provider.complete(copy.deepcopy(messages), temperature)
        except Exception as exc:
            event.error = type(exc).__name__
            event.latency_ms = round((time.perf_counter() - started) * 1000, 1)
            raise
        event.latency_ms = round((time.perf_counter() - started) * 1000, 1)
        event.tokens_in = response.tokens_in or event.tokens_in
        event.tokens_out = response.tokens_out or estimate_tokens(response.text)
        return response
