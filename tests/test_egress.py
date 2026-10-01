"""Egress Gate: blocking rules, privacy modes, budgets and audit records."""

from __future__ import annotations

import time

import pytest

from core.errors import LLMUnavailableError, PrivacyBlockedError
from core.llm.audit import AuditLog
from core.llm.egress import (
    MAX_PAYLOAD_BYTES,
    EgressGate,
    find_sensitive,
    is_private_endpoint,
    redact,
)
from tests.fakes import FakeProvider, make_settings

MESSAGES = [{"role": "user", "content": "Total revenue by month?"}]


def gate(
    provider: FakeProvider | None = None, **settings
) -> tuple[EgressGate, AuditLog, FakeProvider]:
    """A gate wired to a fresh audit log and a fake provider."""
    provider = provider or FakeProvider(["ok"])
    audit = AuditLog()
    return EgressGate(make_settings(**settings), audit, provider), audit, provider


def test_clean_payload_is_sent_and_audited() -> None:
    egress, audit, provider = gate()
    response = egress.send("nl2sql", MESSAGES)
    assert response.text == "ok"
    assert len(provider.calls) == 1
    (event,) = audit.events
    assert not event.blocked and event.external
    assert event.tokens_in == 10 and event.tokens_out == 5
    assert audit.total_bytes_sent == event.payload_bytes > 0


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Write to anna.lee@example.com about it", "email"),
        ("card 4111 1111 1111 1111 was declined", "card number"),
        ("card 4111111111111111", "card number"),
        ("call +998 90 123 45 67 today", "phone number"),
        ("call (415) 555-2671 today", "phone number"),
        ("call 415-555-2671 today", "phone number"),
    ],
)
def test_sensitive_payload_is_blocked_and_redacted(text: str, kind: str) -> None:
    egress, audit, provider = gate()
    with pytest.raises(PrivacyBlockedError):
        egress.send("nl2sql", [{"role": "user", "content": text}])
    assert provider.calls == []
    (event,) = audit.events
    assert event.blocked and kind in (event.block_reason or "")
    assert f"[REDACTED {kind}]" in event.payload_text
    assert audit.blocked_count == 1
    assert audit.total_bytes_sent == 0


@pytest.mark.parametrize(
    "text",
    [
        "revenue grew +12.5% versus 2026-09-01",
        "period 2026-09-01 12:30:00 to 2026-09-30",
        "order 1234567890123456 not a card",
        "SUM(amount) = 1234567890123.45",
        "top 5 stores by revenue in Q3 2026",
    ],
)
def test_ordinary_text_is_not_flagged(text: str) -> None:
    assert find_sensitive(text) == []


@pytest.mark.parametrize("chunk", ["x", "word ", "a.b-c+d", "12 34 "])
def test_scan_of_a_large_payload_is_fast(chunk: str) -> None:
    text = chunk * (190_000 // len(chunk))
    started = time.perf_counter()
    find_sensitive(text)
    assert time.perf_counter() - started < 2.0


def test_redact_replaces_every_match() -> None:
    text = "a@b.co and a@b.co"
    assert "a@b.co" not in redact(text, find_sensitive(text))


def test_local_mode_requires_a_private_endpoint() -> None:
    provider = FakeProvider(["ok"])
    provider.api_base = "https://api.example.com"
    egress, audit, _ = gate(provider, privacy_mode="local", llm_api_base="https://api.example.com")
    with pytest.raises(PrivacyBlockedError):
        egress.send("nl2sql", MESSAGES)
    assert provider.calls == [] and audit.blocked_count == 1


def test_local_mode_with_localhost_is_allowed_and_not_external() -> None:
    provider = FakeProvider(["ok"])
    provider.api_base = "http://localhost:11434"
    egress, audit, _ = gate(provider, privacy_mode="local", llm_api_base="http://localhost:11434")
    egress.send("nl2sql", MESSAGES)
    assert len(provider.calls) == 1
    assert audit.total_bytes_sent == 0
    assert not audit.events[0].external


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("http://localhost:11434", True),
        ("http://127.0.0.1:8080/v1", True),
        ("192.168.1.20:8000", True),
        ("http://10.0.0.5", True),
        ("http://[::1]:11434", True),
        ("https://api.openai.com", False),
        ("http://8.8.8.8", False),
        ("http://my-server.example.com", False),
        ("", False),
    ],
)
def test_private_endpoint_detection(base: str, expected: bool) -> None:
    assert is_private_endpoint(base) is expected


def test_oversized_payload_is_blocked() -> None:
    egress, audit, provider = gate()
    big = [{"role": "user", "content": "x" * (MAX_PAYLOAD_BYTES + 10)}]
    with pytest.raises(PrivacyBlockedError):
        egress.send("nl2sql", big)
    assert provider.calls == [] and audit.blocked_count == 1


def test_token_budget_stops_further_calls() -> None:
    egress, audit, provider = gate(session_token_budget=60)
    egress.send("nl2sql", MESSAGES)  # 15 tokens used (10 in + 5 out)
    egress.send("nl2sql", MESSAGES)
    egress.send("nl2sql", MESSAGES)
    with pytest.raises(LLMUnavailableError, match="budget"):
        egress.send("nl2sql", [{"role": "user", "content": "y" * 400}])
    assert len(provider.calls) == 3
    assert audit.blocked_count == 1


def test_provider_error_is_recorded() -> None:
    egress, audit, _ = gate(FakeProvider([RuntimeError("boom")]))
    with pytest.raises(RuntimeError):
        egress.send("nl2sql", MESSAGES)
    assert audit.events[0].error == "RuntimeError"


def test_export_is_one_json_object_per_line() -> None:
    egress, audit, _ = gate()
    egress.send("a", MESSAGES)
    egress.send("b", MESSAGES)
    lines = audit.export_jsonl().strip().splitlines()
    assert len(lines) == 2 and all(line.startswith("{") for line in lines)
    audit.clear()
    assert audit.events == [] and audit.export_jsonl() == ""
