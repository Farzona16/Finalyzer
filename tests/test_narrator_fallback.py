"""Template and live narrators: one card per finding, grounding retries, safe fallback."""

from __future__ import annotations

import json

from core.errors import LLMUnavailableError
from core.insights.grounding import ungrounded_in_card
from core.insights.narrator import (
    MAX_RETRIES,
    TemplateNarrator,
    build_card,
    narrate_findings,
)
from core.insights.ranking import split_top
from core.llm.audit import AuditLog
from core.llm.gateway import LLMGateway
from core.models import Finding
from tests.fakes import FakeProvider, make_settings

HEDGES = ("may", "might", "likely", "could", "cannot tell", "does not show", "possible")


def cards_json(findings: list[Finding], mutate=None) -> str:
    """A model reply made from the template cards of the given findings."""
    cards = [build_card(f).model_dump() for f in findings]
    if mutate:
        mutate(cards)
    return json.dumps({"cards": cards})


def gateway_with(replies: list, **settings) -> tuple[LLMGateway, FakeProvider]:
    """A live gateway whose model is a scripted fake."""
    config = make_settings(**settings)
    provider = FakeProvider(replies)
    return LLMGateway(config, AuditLog(), provider), provider


def invent_number(cards: list[dict]) -> None:
    """Make the first card mention a number that no fact supports."""
    cards[0]["observation"] += " Customers fell by 87.3% as well."


def test_template_narrator_writes_one_grounded_card_per_top_finding(retail_findings) -> None:
    top, _ = split_top(retail_findings)
    cards = TemplateNarrator().narrate(top)
    assert [c.finding_id for c in cards] == [f.id for f in top]
    assert len(cards) == 5
    for card, finding in zip(cards, top, strict=True):
        assert ungrounded_in_card(card, finding) == []
        assert card.observation and card.actions and card.likely_cause


def test_all_template_cards_are_grounded_and_hedged(retail_findings) -> None:
    for finding in retail_findings:
        card = build_card(finding)
        assert ungrounded_in_card(card, finding) == [], finding.title
        assert card.expected_impact is None
        assert any(w in card.likely_cause.lower() for w in HEDGES), card.likely_cause
        assert len(card.actions) in (1, 2)


def test_offline_gateway_makes_no_calls_and_uses_templates(retail_findings) -> None:
    config = make_settings(llm_mode="offline")
    gateway = LLMGateway(config, AuditLog())
    result = narrate_findings(retail_findings[:5], gateway, config)
    assert result.source == "template" and result.note is None
    assert len(result.cards) == 5
    assert gateway.audit.events == []


def test_missing_gateway_uses_templates(retail_findings) -> None:
    result = narrate_findings(retail_findings[:3], None, make_settings())
    assert result.source == "template" and len(result.cards) == 3


def test_no_findings_gives_no_cards() -> None:
    assert narrate_findings([], None, make_settings()).cards == []


def test_live_narrator_accepts_grounded_cards(retail_findings) -> None:
    top = retail_findings[:5]
    gateway, provider = gateway_with([cards_json(top)])
    result = narrate_findings(top, gateway, gateway.settings)
    assert result.source == "ai" and result.note is None
    assert [c.finding_id for c in result.cards] == [f.id for f in top]
    assert len(provider.calls) == 1


def test_code_overrides_priority_confidence_and_impact(retail_findings) -> None:
    top = retail_findings[:2]

    def tamper(cards: list[dict]) -> None:
        for card in cards:
            card.update(priority="Low", confidence="Low", expected_impact="It will help.")

    gateway, _ = gateway_with([cards_json(top, tamper)])
    result = narrate_findings(top, gateway, gateway.settings)
    assert result.source == "ai"
    for card, finding in zip(result.cards, top, strict=True):
        assert card.expected_impact is None
        assert (card.priority, card.confidence) == (
            build_card(finding).priority, build_card(finding).confidence
        )


def test_invented_number_is_retried_with_feedback(retail_findings) -> None:
    top = retail_findings[:3]
    gateway, provider = gateway_with([cards_json(top, invent_number), cards_json(top)])
    result = narrate_findings(top, gateway, gateway.settings)
    assert result.source == "ai"
    assert len(provider.calls) == 2
    feedback = provider.calls[1][-1]["content"]
    assert "87.3%" in feedback and "not supported" in feedback


def test_persistent_invented_numbers_fall_back_to_templates(retail_findings) -> None:
    top = retail_findings[:3]
    gateway, provider = gateway_with([cards_json(top, invent_number)])
    result = narrate_findings(top, gateway, gateway.settings)
    assert result.source == "template"
    assert "without AI" in (result.note or "")
    assert len(provider.calls) == MAX_RETRIES + 1
    assert all("87.3%" not in c.observation for c in result.cards)
    assert len(result.cards) == 3


def test_wrong_card_set_is_rejected(retail_findings) -> None:
    top = retail_findings[:3]
    gateway, provider = gateway_with([cards_json(top[:2]), cards_json(top)])
    result = narrate_findings(top, gateway, gateway.settings)
    assert result.source == "ai" and len(provider.calls) == 2


def test_malformed_json_and_empty_output_fall_back(retail_findings) -> None:
    top = retail_findings[:2]
    for bad in ("this is not json", "", "{}", '{"cards": "none"}'):
        gateway, _ = gateway_with([bad])
        result = narrate_findings(top, gateway, gateway.settings)
        assert result.source == "template", bad
        assert "without AI" in (result.note or "")


def test_provider_failures_fall_back(retail_findings) -> None:
    top = retail_findings[:2]
    # The live provider turns every timeout, 429 and 5xx into LLMUnavailableError.
    for error in (LLMUnavailableError("timeout"), LLMUnavailableError("429")):
        gateway, _ = gateway_with([error])
        result = narrate_findings(top, gateway, gateway.settings)
        assert result.source == "template" and len(result.cards) == 2
