"""Canary payloads in Anthropic and Grok shapes, every privacy level (phase V2)."""

from __future__ import annotations

import pytest

from core.errors import PrivacyBlockedError
from core.llm.audit import AuditLog
from core.llm.egress import EgressGate
from core.llm.gateway import LLMGateway
from tests.fakes import FakeProvider, make_settings

CANARY = "canary.bird@example.com"


@pytest.mark.parametrize("level", ["strict", "balanced", "open"])
@pytest.mark.parametrize("name,base", [("anthropic", "https://api.anthropic.com"), ("grok", "https://api.x.ai")])
def test_a_canary_email_is_blocked_for_each_provider_shape(level: str, name: str, base: str) -> None:
    settings = make_settings(privacy_level=level, privacy_mode="demo")
    provider = FakeProvider(name=name)
    provider.api_base = base
    audit = AuditLog()
    gate = EgressGate(settings, audit, provider)
    with pytest.raises(PrivacyBlockedError):
        gate.send("canary", [{"role": "user", "content": f"rows for {CANARY}"}])
    assert audit.blocked_count == 1
    assert provider.calls == []
    assert CANARY not in (audit.events[0].payload_text or "")


def test_real_data_does_not_reach_a_synthetic_only_provider() -> None:
    settings = make_settings(
        privacy_mode="cloud",
        provider_clearance="anthropic:synthetic_only,fake:synthetic_only",
    )
    provider = FakeProvider(["{}"], name="fake")
    gateway = LLMGateway(settings, AuditLog(), provider, data_origin="real")
    with pytest.raises(Exception, match="cleared|template|Blocked"):  # noqa: BLE001
        gateway.complete_text("smoke", [{"role": "user", "content": "hello"}])
    assert provider.calls == []
