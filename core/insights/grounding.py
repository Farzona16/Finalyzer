"""Grounding: every number in a card must come from the finding's facts.

Documented behaviour:
- Recognised forms: ``1,234.5``, ``12%``, ``12.0 %``, ``3.2x``, ``$5k``, ``4.51M``,
  ``2 million``, ``3.0 points``. Number words ("twelve percent") are not parsed.
- A number is grounded when it equals a fact rounded to the number of decimals shown (in the
  unit shown, e.g. millions), or lies within 0.5% of a fact. The sign is ignored.
- Ignored: plain integers 0-10, years 1900-2100, dates and months that occur in the facts,
  and digits inside category, metric and column names.
- Only the numbers in the facts are allowed; the code adds no other derived values, so the
  narrator must not compute new numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.models import Finding, RecommendationCard

RELATIVE_TOLERANCE = 0.005
SCALES = {
    "k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9,
    "t": 1e12, "trillion": 1e12,
}
UNIT_MARKERS = {"%", "x", "pp", "pts", "point", "points"}
DATE_RE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
NUMBER_RE = re.compile(
    r"(?<![\w.])[$€£]?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s?(?P<suffix>%|x\b|pp\b|pts\b|points?\b|thousand\b|million\b|billion\b|trillion\b|"
    r"bn\b|[kKmMbBtT]\b)?"
)


@dataclass(frozen=True)
class NumberToken:
    """A number found in text: what is written and what it means."""

    text: str
    mantissa: float
    scale: float
    decimals: int
    value: float
    marked: bool  # has a unit or scale suffix


def name_variants(finding: Finding) -> list[str]:
    """Strings whose digits must not be read as numbers (category, metric, column names)."""
    names = [v for v in finding.facts.values() if isinstance(v, str)]
    names += list((finding.segment or {}).values()) + list((finding.segment or {}).keys())
    return sorted({n for n in names if n and any(ch.isdigit() for ch in n)}, key=len, reverse=True)


def strip_names(text: str, finding: Finding) -> str:
    """Remove category/metric names (they may contain digits, e.g. ``Store 03``)."""
    for name in name_variants(finding):
        text = re.sub(re.escape(name), " ", text, flags=re.IGNORECASE)
    return text


def fact_dates(finding: Finding) -> str:
    """All string facts joined; dates in text must occur here."""
    return " ".join(str(v) for v in finding.facts.values() if isinstance(v, str))


def undated(text: str, finding: Finding) -> tuple[str, list[str]]:
    """Remove dates/months from text; return the text and the dates that are not in the facts."""
    known = fact_dates(finding)
    unknown = [d for d in DATE_RE.findall(text) if d not in known]
    return DATE_RE.sub(" ", text), unknown


def extract_numbers(text: str) -> list[NumberToken]:
    """All numbers in a text with their meaning (scale, decimals, unit)."""
    tokens = []
    for match in NUMBER_RE.finditer(text):
        raw = match.group("num")
        suffix = (match.group("suffix") or "").lower()
        mantissa = float(raw.replace(",", ""))
        scale = SCALES.get(suffix, 1.0)
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        tokens.append(
            NumberToken(match.group(0).strip(), mantissa, scale, decimals, mantissa * scale,
                        bool(suffix))
        )
    return tokens


def is_ignored(token: NumberToken) -> bool:
    """Plain integers 0-10 and years 1900-2100 are not checked."""
    if token.marked or token.decimals or token.scale != 1.0:
        return False
    whole = token.mantissa
    return 0 <= whole <= 10 or (1900 <= whole <= 2100 and "," not in token.text)


def matches(token: NumberToken, fact: float) -> bool:
    """True when the token equals the fact at the displayed precision or within 0.5%."""
    fact = abs(fact)
    shown = round(fact / token.scale, token.decimals)
    if abs(shown - token.mantissa) < 1e-9:
        return True
    if fact == 0:
        return token.value == 0
    return abs(token.value - fact) / fact <= RELATIVE_TOLERANCE


def numeric_facts(finding: Finding) -> list[float]:
    """The numeric facts of a finding, as absolute values."""
    return [
        abs(float(v)) for v in finding.facts.values()
        if isinstance(v, int | float) and not isinstance(v, bool)
    ]


def ungrounded_in_text(text: str, finding: Finding) -> list[str]:
    """Numbers and dates in ``text`` that the finding's facts do not support."""
    cleaned, bad_dates = undated(strip_names(text, finding), finding)
    allowed = numeric_facts(finding)
    bad = [
        t.text for t in extract_numbers(cleaned)
        if not is_ignored(t) and not any(matches(t, fact) for fact in allowed)
    ]
    return bad_dates + bad


def card_texts(card: RecommendationCard) -> list[str]:
    """Every text field of a card."""
    texts = [card.observation, *card.evidence, card.likely_cause, *card.actions, card.track_metric]
    if card.expected_impact:
        texts.append(card.expected_impact)
    return texts


def ungrounded_in_card(card: RecommendationCard, finding: Finding) -> list[str]:
    """Numbers in a card that are not supported by its finding (empty list = grounded)."""
    bad: list[str] = []
    for text in card_texts(card):
        bad += ungrounded_in_text(text, finding)
    return bad
