"""Cloud mode: small groups never reach the model and category values are masked by default."""

from __future__ import annotations

import json

from core.insights.narrator import build_card, narrate_findings
from core.insights.privacy import (
    SegmentMasker,
    cloud_safe,
    is_small,
    prepare_for_model,
    strip_small_groups,
)
from core.llm.audit import AuditLog
from core.llm.gateway import LLMGateway
from core.models import Finding
from tests.fakes import FakeProvider, make_settings

TINY_STORE = "Tiny Outlet Q9"


def tiny_finding(support: int = 3) -> Finding:
    """A finding about a very small group (below K_ANON_MIN in cloud mode)."""
    return Finding(
        id=f"driver:revenue:{TINY_STORE}", type="driver", metric_id="revenue",
        title=f"{TINY_STORE} explains 55% of the revenue drop",
        facts={
            "metric_label": "Revenue", "unit": "currency", "dimension": "store_name",
            "segment": TINY_STORE, "segment_change": -1200.0, "total_change": -2200.0,
            "share_of_change_pct": 55.0, "segment_level_share_pct": 12.0, "categories": 9.0,
            "direction": "down", "assessment": "bad", "last_period": "2026-09",
            "previous_period": "2026-08",
        },
        segment={"store_name": TINY_STORE}, period_label="2026-09", support=support,
        severity=0.9, confidence=0.9,
    )


def cloud_gateway(findings: list[Finding], **settings) -> tuple[LLMGateway, FakeProvider]:
    """A cloud-mode gateway whose fake model answers with template cards of masked findings."""
    config = make_settings(privacy_mode="cloud", **settings)

    def reply(messages: list[dict[str, str]], temperature: float) -> str:
        sent = json.loads(messages[-1]["content"])["findings"]
        safe, _ = prepare_for_model(findings, config)
        by_alias = {f"f{i}": f for i, f in enumerate(safe, 1)}
        cards = []
        for item in sent:
            card = build_card(by_alias[item["id"]]).model_dump()
            card["finding_id"] = item["id"]
            cards.append(card)
        return json.dumps({"cards": cards})

    provider = FakeProvider([reply])
    return LLMGateway(config, AuditLog(), provider), provider


def all_payloads(gateway: LLMGateway) -> str:
    """Everything the audit log recorded as payload, in one string."""
    return "\n".join(event.payload_text for event in gateway.audit.events)


def test_small_findings_are_detected_in_cloud_mode_only() -> None:
    small = tiny_finding()
    assert is_small(small, make_settings(privacy_mode="cloud"))
    assert not is_small(small, make_settings(privacy_mode="demo"))
    assert not is_small(tiny_finding(support=5), make_settings(privacy_mode="cloud"))


def test_cloud_payload_has_no_finding_below_k(retail_findings) -> None:
    findings = [*retail_findings[:4], tiny_finding()]
    gateway, provider = cloud_gateway(findings)
    result = narrate_findings(findings, gateway, gateway.settings)
    payloads = all_payloads(gateway)
    assert len(provider.calls) == 1
    assert TINY_STORE not in payloads
    assert "Tiny" not in payloads and "55%" not in payloads and "-1200" not in payloads
    assert payloads.count('\\"id\\": \\"f') == 4
    # The small finding still gets a card, written from the template, and the note says why.
    assert len(result.cards) == 5
    assert any(TINY_STORE in c.observation for c in result.cards)
    assert result.source == "ai" and "too small" in (result.note or "")


def test_only_small_findings_send_nothing(retail_findings) -> None:
    gateway, provider = cloud_gateway([tiny_finding(), tiny_finding(support=1)])
    result = narrate_findings([tiny_finding()], gateway, gateway.settings)
    assert provider.calls == [] and gateway.audit.events == []
    assert result.source == "template" and len(result.cards) == 1


def test_category_values_are_masked_by_default(retail_findings) -> None:
    findings = list(retail_findings)
    gateway, _ = cloud_gateway(findings)
    result = narrate_findings(findings, gateway, gateway.settings)
    payloads = all_payloads(gateway)
    names = {v for f in findings for v in SegmentMasker.segment_values(f)}
    assert names, "the sample must contain category values"
    for name in names:
        assert name not in payloads, name
    assert "Segment A" in payloads
    shown = " ".join(c.observation for c in result.cards)
    assert "Segment A" not in shown
    assert result.source == "ai"


def test_category_values_are_sent_when_the_operator_allows_it(retail_findings) -> None:
    findings = [f for f in retail_findings if f.type == "concentration"][:1]
    gateway, _ = cloud_gateway(findings, send_category_values=True)
    narrate_findings(findings, gateway, gateway.settings)
    assert "Electronics" in all_payloads(gateway)
    assert "Segment A" not in all_payloads(gateway)


def test_masking_round_trip() -> None:
    masker = SegmentMasker()
    masked = masker.mask_finding(tiny_finding())
    assert TINY_STORE not in masked.title
    assert masked.segment == {"store_name": "Segment A"}
    assert masked.facts["segment"] == "Segment A"
    assert masker.unmask("Look at Segment A first.") == f"Look at {TINY_STORE} first."


def test_masker_labels_are_stable_and_distinct() -> None:
    masker = SegmentMasker()
    labels = [masker.label(f"value {i}") for i in range(30)]
    assert len(set(labels)) == 30
    assert masker.label("value 0") == labels[0]
    assert labels[26] == "Segment A1"
    assert masker.unmask("Segment A1 and Segment A") == "value 26 and value 0"


def test_runner_up_group_below_k_is_removed_from_facts() -> None:
    base = tiny_finding(support=500)
    facts = {**base.facts, "second_segment": "Small One", "second_support": 2.0,
             "second_value": 10.0}
    finding = base.model_copy(update={"facts": facts})
    cloud = make_settings(privacy_mode="cloud")
    stripped = strip_small_groups(finding, cloud)
    assert not any(k.startswith("second_") for k in stripped.facts)
    assert strip_small_groups(finding, make_settings(privacy_mode="demo")).facts == facts
    enough = finding.model_copy(update={"facts": {**facts, "second_support": 50.0}})
    assert "second_support" in strip_small_groups(enough, cloud).facts


def test_cloud_safe_drops_small_findings_only_in_cloud() -> None:
    findings = [tiny_finding(), tiny_finding(support=400)]
    assert len(cloud_safe(findings, make_settings(privacy_mode="cloud"))) == 1
    assert len(cloud_safe(findings, make_settings(privacy_mode="demo"))) == 2


def test_real_findings_are_big_enough_for_cloud(retail_findings) -> None:
    cloud = make_settings(privacy_mode="cloud")
    assert [f.title for f in retail_findings if is_small(f, cloud)] == []
