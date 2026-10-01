"""Grounding: every number in a card must come from the finding's facts."""

from __future__ import annotations

import pytest

from core.insights.grounding import (
    extract_numbers,
    ungrounded_in_card,
    ungrounded_in_text,
)
from core.models import Finding, RecommendationCard


def make_finding(**facts: float | str) -> Finding:
    """A trend-like finding with a few numeric facts."""
    base: dict[str, float | str] = {
        "metric_label": "Revenue", "pct_change": -12.0, "last_value": 4_510_000.0,
        "previous_value": 5_125_000.0, "last_period": "2026-09", "ratio": 3.2,
        "share": 47.3, "count": 1234.5,
    }
    base.update(facts)
    return Finding(
        id="f1", type="trend", metric_id="revenue", title="Revenue fell", facts=base,
        period_label="2026-09", support=500, severity=0.5, confidence=0.9,
    )


def card(text: str, **fields: object) -> RecommendationCard:
    """A card whose observation is the given text."""
    data = {
        "finding_id": "f1", "observation": text, "evidence": [], "likely_cause": "It may be x.",
        "actions": ["Check it."], "expected_impact": None, "priority": "High",
        "confidence": "High", "track_metric": "Revenue",
    }
    data.update(fields)
    return RecommendationCard.model_validate(data)


@pytest.mark.parametrize(
    "text",
    [
        "Revenue fell 12%.",
        "Revenue fell 12.0 %.",
        "Revenue fell -12%.",
        "Revenue fell by 12.04%.",
        "Revenue is 4.51M.",
        "Revenue is 4.51 million.",
        "Revenue was $4,510,000.",
        "Revenue was 5.1M before.",
        "That is 3.2x the level.",
        "The top group holds 47% of revenue.",
        "Found 1,234.5 rows.",
        "In 2026-09 revenue fell, 2 stores and 7 products were checked.",
        "Since 2025 it fell 12%.",
    ],
)
def test_supported_numbers_pass(text: str) -> None:
    assert ungrounded_in_text(text, make_finding()) == []


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Revenue fell 15%.", "15%"),
        ("Revenue fell 12% and cost rose 33%.", "33%"),
        ("Revenue is 9.9M.", "9.9M"),
        ("Revenue was $5k.", "$5k"),
        ("That is 8.5x the level.", "8.5x"),
        ("We saw 1,999 rows.", "1,999"),
        ("Revenue fell on 2026-08-12.", "2026-08-12"),
    ],
)
def test_invented_numbers_fail(text: str, expected: str) -> None:
    assert expected in ungrounded_in_text(text, make_finding())


def test_small_integers_and_years_are_ignored() -> None:
    text = "Between 1 and 10 stores changed in 1999 and 2050."
    assert ungrounded_in_text(text, make_finding()) == []


def test_digits_inside_names_are_not_numbers() -> None:
    finding = make_finding(segment="Store 03", metric_label="Gross margin % (v2)")
    text = "Store 03 explains 12% of the change in Gross margin % (v2)."
    assert ungrounded_in_text(text, finding) == []


def test_number_words_are_not_parsed() -> None:
    """Documented limitation: 'twelve percent' is not a number for the checker."""
    assert ungrounded_in_text("Revenue fell twelve percent.", make_finding()) == []


def test_extract_numbers_reads_scale_and_precision() -> None:
    tokens = {t.text: t for t in extract_numbers("1,234.5 and 4.51M and 2 million and 12.0 %")}
    assert tokens["1,234.5"].value == 1234.5
    assert tokens["4.51M"].value == 4_510_000
    assert tokens["2 million"].value == 2_000_000
    assert tokens["12.0 %"].decimals == 1


def test_card_with_invented_number_fails_and_correct_card_passes() -> None:
    finding = make_finding()
    good = card("Revenue fell 12% in 2026-09.", evidence=["From 5.1M to 4.51M."])
    bad = card("Revenue fell 12% in 2026-09.", evidence=["Customers dropped by 40%."])
    assert ungrounded_in_card(good, finding) == []
    assert ungrounded_in_card(bad, finding) == ["40%"]


def test_every_text_field_is_checked() -> None:
    finding = make_finding()
    for field, value in [
        ("likely_cause", "It may be a 25% price change."),
        ("actions", ["Cut prices by 25%."]),
        ("track_metric", "Revenue 25%"),
        ("expected_impact", "About 25% more."),
    ]:
        assert ungrounded_in_card(card("Revenue fell 12%.", **{field: value}), finding), field
