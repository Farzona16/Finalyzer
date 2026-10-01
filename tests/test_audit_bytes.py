"""What the audit log shows is byte-for-byte what the provider received."""

from __future__ import annotations

import json

from core.llm.audit import AuditLog
from core.llm.egress import serialize_payload
from core.llm.gateway import LLMGateway
from core.metrics.registry import available_metrics
from core.query.nl2sql import NL2SQLAgent
from tests.fakes import FakeProvider, make_settings, query_reply


def test_audited_payload_equals_received_payload(retail) -> None:
    settings = make_settings()
    provider = FakeProvider([query_reply("SELECT COUNT(*) AS n FROM data")])
    audit = AuditLog()
    gateway = LLMGateway(settings, audit, provider)
    agent = NL2SQLAgent(
        retail.con, retail.model, gateway, settings, available_metrics(retail.model), "retail"
    )
    answer = agent.ask("How many sales rows are there in total?")
    assert answer.status == "ok"
    events = audit.events
    assert len(events) == len(provider.calls) == 3
    received = sorted(serialize_payload(call) for call in provider.calls)
    audited = sorted(e.payload_text for e in events)
    assert audited == received
    assert all(e.payload_bytes == len(e.payload_text.encode("utf-8")) for e in events)
    assert audit.total_bytes_sent == sum(len(p.encode("utf-8")) for p in received)


def test_payload_is_valid_json_of_the_chat_messages(retail) -> None:
    settings = make_settings()
    provider = FakeProvider([query_reply("SELECT 1 AS x")])
    audit = AuditLog()
    gateway = LLMGateway(settings, audit, provider)
    gateway.complete_text("probe", [{"role": "user", "content": "Grüße – ok"}])
    assert json.loads(audit.events[0].payload_text) == provider.calls[0]
