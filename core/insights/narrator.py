"""Turn findings into recommendation cards: template wording or LLM wording (grounded).

Both narrators only put words around numbers that the detectors computed. The LLM version
sends only the findings JSON (never rows), checks every number in the answer against the
facts, retries up to two times, and falls back to the templates if that does not work.
"""

from __future__ import annotations

import json
import logging
from typing import Literal, Protocol

from pydantic import BaseModel

from core.config import Settings
from core.errors import UserFacingError
from core.insights.actions import ACTION_TEMPLATES
from core.insights.grounding import ungrounded_in_card
from core.insights.privacy import SegmentMasker, is_small, prepare_for_model
from core.insights.ranking import confidence_level, priority_level
from core.insights.render import describe
from core.llm.gateway import LLMGateway
from core.models import Finding, RecommendationCard

logger = logging.getLogger("finalyzer.narrator")

MAX_RETRIES = 2
NARRATOR_SYSTEM_PROMPT = (
    "You write business insight cards from verified findings. Use only numbers present in "
    "`facts`. Do not compute new numbers. Describe causes as hypotheses. Do not promise "
    "outcomes. Each card has: finding_id, observation, evidence (list of short strings quoting "
    "given facts), likely_cause, actions (1-2 concrete steps), expected_impact (only if a "
    "number is provided for it, otherwise null), priority, confidence, track_metric. Return "
    'JSON: {"cards": [ ... ]} matching the schema, one card per finding in the given order. '
    "Write in English, plain and specific, no filler."
)


class CardsReply(BaseModel):
    """The JSON the model must return."""

    cards: list[RecommendationCard]


class NarrationResult(BaseModel):
    """Cards plus how they were written."""

    cards: list[RecommendationCard]
    source: Literal["ai", "template"]
    note: str | None = None


class Narrator(Protocol):
    """Anything that can write cards for findings."""

    def narrate(self, findings: list[Finding]) -> list[RecommendationCard]:
        """One card per finding."""
        ...


class NarrationFailed(Exception):
    """The model could not produce grounded cards."""


def build_card(finding: Finding) -> RecommendationCard:
    """A template card for one finding."""
    parts = describe(finding)
    return RecommendationCard(
        finding_id=finding.id,
        observation=parts.observation,
        evidence=parts.evidence,
        likely_cause=parts.likely_cause,
        actions=parts.actions,
        expected_impact=None,
        priority=priority_level(finding),
        confidence=confidence_level(finding),
        track_metric=parts.track_metric,
    )


class TemplateNarrator:
    """Deterministic wording from templates; makes no model calls."""

    def narrate(self, findings: list[Finding]) -> list[RecommendationCard]:
        """One card per finding, in the given order."""
        return [build_card(f) for f in findings]


def finding_payload(finding: Finding) -> dict:
    """The part of a finding a model sees."""
    return {
        "id": finding.id,
        "type": finding.type,
        "metric": finding.facts.get("metric_label", finding.metric_id),
        "direction": finding.facts.get("direction"),
        "assessment": finding.facts.get("assessment"),
        "facts": finding.facts,
        "segment": finding.segment,
        "period": finding.period_label,
    }


def build_messages(findings: list[Finding]) -> list[dict[str, str]]:
    """Chat messages with the findings JSON and the action templates as hints."""
    types = {f.type for f in findings}
    hints = {t: v for (t, _), v in ACTION_TEMPLATES.items() if t in types}
    body = json.dumps(
        {"findings": [finding_payload(f) for f in findings], "action_hints": hints},
        ensure_ascii=False,
    )
    return [
        {"role": "system", "content": NARRATOR_SYSTEM_PROMPT},
        {"role": "user", "content": body},
    ]


class LiveNarrator:
    """Wording by an LLM, checked against the facts."""

    def __init__(self, gateway: LLMGateway, settings: Settings) -> None:
        self._gateway = gateway
        self._settings = settings

    def narrate(self, findings: list[Finding]) -> list[RecommendationCard]:
        """Grounded cards for every finding or ``NarrationFailed`` after the retries."""
        findings = [f for f in findings if not is_small(f, self._settings)]
        sendable, masker = prepare_for_model(findings, self._settings)
        # Real ids can contain category values, so the model only sees neutral aliases.
        aliases = {f"f{i}": f.id for i, f in enumerate(findings, 1)}
        renamed = [s.model_copy(update={"id": a}) for a, s in zip(aliases, sendable, strict=True)]
        messages = build_messages(renamed)
        problem = ""
        for _ in range(MAX_RETRIES + 1):
            try:
                reply = self._gateway.complete_json("narrate", messages, CardsReply)
            except UserFacingError as exc:
                raise NarrationFailed(exc.message) from exc
            cards, problem = self.check(reply, findings, masker, aliases)
            if not problem:
                return cards
            messages = [*messages, {"role": "assistant", "content": reply.model_dump_json()},
                        {"role": "user", "content": f"{problem} Return the corrected JSON only."}]
        raise NarrationFailed(problem)

    @staticmethod
    def check(
        reply: CardsReply, findings: list[Finding], masker: SegmentMasker | None,
        aliases: dict[str, str] | None = None,
    ) -> tuple[list[RecommendationCard], str]:
        """Restore ids and names, fix code-owned fields and verify every number."""
        aliases = aliases or {}
        by_id = {aliases.get(c.finding_id, c.finding_id): c for c in reply.cards}
        if set(by_id) != {f.id for f in findings} or len(reply.cards) != len(findings):
            return [], "The cards must match the findings exactly, one card per finding_id."
        cards, bad = [], []
        for finding in findings:
            card = restore(by_id[finding.id], finding, masker)
            problems = ungrounded_in_card(card, finding)
            bad += [f"{finding.id}: {p}" for p in problems]
            cards.append(card)
        if bad:
            return [], "These numbers are not supported by the facts: " + "; ".join(bad[:8]) + "."
        return cards, ""


def restore(
    card: RecommendationCard, finding: Finding, masker: SegmentMasker | None
) -> RecommendationCard:
    """Unmask names and replace the fields that code, not the model, decides."""
    data = card.model_dump()
    data["finding_id"] = finding.id
    if masker is not None:
        for key in ("observation", "likely_cause", "track_metric"):
            data[key] = masker.unmask(data[key])
        data["evidence"] = [masker.unmask(t) for t in data["evidence"]]
        data["actions"] = [masker.unmask(t) for t in data["actions"]]
    data["expected_impact"] = None
    data["priority"] = priority_level(finding)
    data["confidence"] = confidence_level(finding)
    return RecommendationCard.model_validate(data)


def narrate_findings(
    findings: list[Finding], gateway: LLMGateway | None, settings: Settings
) -> NarrationResult:
    """Cards for the given findings: AI-written when possible, template-written otherwise."""
    template = TemplateNarrator()
    if not findings:
        return NarrationResult(cards=[], source="template")
    if gateway is None or gateway.is_offline:
        return NarrationResult(cards=template.narrate(findings), source="template")
    shareable = [f for f in findings if not is_small(f, settings)]
    if not shareable:
        return NarrationResult(
            cards=template.narrate(findings), source="template",
            note="Template-written: these groups are too small to share with an AI service.",
        )
    try:
        ai_cards = {c.finding_id: c for c in LiveNarrator(gateway, settings).narrate(shareable)}
    except NarrationFailed:
        logger.warning("narration_fallback to_template=true")
        return NarrationResult(
            cards=template.narrate(findings), source="template",
            note="Generated without AI: the AI wording could not be verified against the data.",
        )
    cards = [ai_cards.get(f.id) or build_card(f) for f in findings]
    note = None
    if len(shareable) != len(findings):
        note = (
            "Some cards are template-written because their groups are too small "
            "to share with an AI service."
        )
    return NarrationResult(cards=cards, source="ai" if ai_cards else "template", note=note)
