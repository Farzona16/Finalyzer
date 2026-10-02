"""Optional AI rephrasing of Uzbek cards (spec 5.6). Off unless ``UZ_POLISH=true``.

The model sees one card's three sentences (never rows) and must return the same three sentences
worded more naturally. A rewrite is accepted only when all of this holds, otherwise the frame
text is kept:

1. every number is one of the original's (Uzbek and English number styles are understood);
2. the direction words match the original's;
3. every «quoted» name of the original is still there, unchanged;
4. the jargon lint finds no technical term (glossary tokens must be kept);
5. each sentence is at most 120% as long as the original.

In ``cloud`` mode the privacy rules of the narrator apply: findings on fewer than ``K_ANON_MIN``
rows are not sent, and category values are replaced by neutral labels unless allowed.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import replace

from pydantic import BaseModel

from core.config import Settings
from core.i18n.jargon import TOKEN_RE, find_jargon
from core.insights.grounding_v3 import problems
from core.insights.plain import PlainCard
from core.insights.privacy import SegmentMasker, is_small
from core.llm.gateway import LLMGateway
from core.models import Finding

logger = logging.getLogger("finalyzer.polish")

MAX_LENGTH_RATIO = 1.2
FIELDS = ("headline", "meaning", "next_step")
QUOTE_RE = re.compile(r"\u00ab([^\u00bb]*)\u00bb")
CATEGORY_KEYS = (
    "segment",
    "best",
    "worst",
    "high",
    "low",
    "category",
    "top_segment",
    "high_segment",
    "low_segment",
    "second_segment",
    "stage_from",
    "stage_to",
)
SYSTEM_PROMPT = (
    "You rephrase three short Uzbek sentences of a business insight card so that they read "
    "naturally. Keep every number, every \u00abquoted\u00bb name and every [[token|text]] exactly "
    "as written. Do not add numbers, reasons, advice or new facts. Keep the direction "
    "(oshdi, kamaydi ...) of the original. Use plain everyday words, no technical terms. Do not "
    'make a sentence longer than 120% of the original. Return JSON only: {"headline": "...", '
    '"meaning": "...", "next_step": "..."}.'
)


class PolishReply(BaseModel):
    """What the model must return."""

    headline: str
    meaning: str
    next_step: str


def category_values(finding: Finding) -> set[str]:
    """Category values that occur in a finding (they are masked before leaving the machine)."""
    values = set((finding.segment or {}).values())
    values |= {
        str(v) for k, v in finding.facts.items() if k in CATEGORY_KEYS and isinstance(v, str)
    }
    return {v for v in values if v}


def check(card: PlainCard, reply: PolishReply, names: list[str]) -> list[str]:
    """The reasons a rewrite cannot be used (empty list = accepted)."""
    found: list[str] = []
    for field in FIELDS:
        before, after = getattr(card, field), getattr(reply, field)
        if not after.strip():
            return [f"{field} is empty"]
        if len(after) > MAX_LENGTH_RATIO * len(before):
            found.append(f"{field} is longer than 120% of the original")
        if sorted(QUOTE_RE.findall(before)) != sorted(QUOTE_RE.findall(after)):
            found.append(f"{field}: the quoted names changed")
        if sorted(TOKEN_RE.findall(before)) != sorted(TOKEN_RE.findall(after)):
            found.append(f"{field}: a glossary token changed")
        if find_jargon(after, card.locale):
            found.append(f"{field}: technical term")
    original = " ".join(getattr(card, f) for f in FIELDS)
    rewritten = " ".join(getattr(reply, f) for f in FIELDS)
    return found + problems(original, rewritten, card.locale, names)


def mask_card(
    card: PlainCard, finding: Finding, settings: Settings
) -> tuple[dict[str, str], SegmentMasker | None]:
    """The sentences to send, with category values masked when they may not leave."""
    _ = settings
    texts = {field: getattr(card, field) for field in FIELDS}
    masker = SegmentMasker()
    values = category_values(finding)
    return {k: masker.mask_text(v, values) for k, v in texts.items()}, masker


def polish_card(
    card: PlainCard, finding: Finding, gateway: LLMGateway | None, settings: Settings
) -> PlainCard:
    """The card with AI-rephrased sentences, or the unchanged card when anything is off."""
    if not settings.uz_polish or card.locale != "uz" or card.level == "expert":
        return card
    if gateway is None or gateway.is_offline or is_small(finding, settings):
        return card
    texts, masker = mask_card(card, finding, settings)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(texts, ensure_ascii=False)},
    ]
    try:
        reply = gateway.complete_json("polish", messages, PolishReply)
    except Exception as exc:  # noqa: BLE001 - any failure keeps the frame text
        logger.warning("polish_failed type=%s", type(exc).__name__)
        return card
    data = reply.model_dump()
    if masker is not None:
        data = {k: masker.unmask(v) for k, v in data.items()}
    restored = PolishReply(**data)
    reasons = check(card, restored, sorted(category_values(finding)))
    if reasons:
        logger.info("polish_refused reasons=%s", "; ".join(reasons[:3]))
        return card
    return replace(card, polished=True, **data)
