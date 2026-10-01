"""Plain-language insight cards from message frames (spec sections 5.4 and 8).

The text of a card is a frame (``core/i18n/frames_*.json``) filled with values that code has
formatted from a finding's facts; nothing is generated freely. A card can be shown at three
levels: ``simple`` (default), ``expert`` (adds the method and the numbers) and ``simpler``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from core.i18n.formats import (
    EMPTY,
    MINUS,
    format_currency,
    format_date,
    format_decimal,
    format_integer,
    format_percent,
)
from core.i18n.frames import FINDING_TYPES, FrameError, compare_text, render_card
from core.i18n.glossary import find_entry
from core.i18n.jargon import TOKEN_RE, find_jargon
from core.insights.ranking import confidence_level, priority_level
from core.models import Finding, Level, Metric

Values = dict[str, object]
TEST_NAMES = {
    "uz": {"welch": "Welch t-testi", "anova": "dispersiya tahlili (ANOVA)"},
    "en": {"welch": "Welch t-test", "anova": "one-way ANOVA"},
}
SMALL_P = 0.001


@dataclass(frozen=True)
class PlainContext:
    """What the wording needs besides the finding: language, currency and the metrics."""

    locale: str = "uz"
    currency: str = "UZS"
    metrics: dict[str, Metric] = field(default_factory=dict)


@dataclass
class PlainCard:
    """One insight card in one language and at one level."""

    finding_id: str
    type: str
    direction: str
    level: str
    locale: str
    headline: str
    meaning: str
    next_step: str
    details: str
    evidence: list[str]
    priority: Level
    confidence: Level
    assessment: str
    polished: bool = False  # the "AI-polished" badge

    def texts(self) -> list[str]:
        """Every text the card shows."""
        return [t for t in (self.headline, self.meaning, self.next_step, self.details) if t]


class Fmt:
    """Number, date and label formatting for one context."""

    def __init__(self, ctx: PlainContext) -> None:
        self.ctx = ctx
        self.locale = ctx.locale

    def num(self, value: object, digits: int = 1) -> str:
        """A plain number without trailing zeros: ``12``, ``12,5``."""
        number = float(value)  # type: ignore[arg-type]
        if round(number, digits) == int(round(number, digits)):
            return format_integer(round(number, digits), self.locale)
        return format_decimal(round(number, digits), self.locale, digits)

    def absnum(self, value: object, digits: int = 1) -> str:
        """A number shown without its sign (the sentence carries the direction)."""
        return self.num(abs(float(value)), digits)  # type: ignore[arg-type]

    def value(self, number: object, unit: str) -> str:
        """A metric value with its unit."""
        if number is None:
            return EMPTY
        amount = float(number)  # type: ignore[arg-type]
        if unit == "currency":
            return format_currency(amount, self.locale, self.ctx.currency)
        if unit == "percent":
            return format_percent(amount, self.locale)
        return self.num(amount, 2 if unit == "ratio" else 1)

    def signed(self, number: float, unit: str) -> str:
        """A value with an explicit minus sign when negative."""
        text = self.value(abs(number), unit)
        return MINUS + text if number < 0 else text

    def day(self, value: object) -> str:
        """A date in the shared style."""
        return format_date(value, self.locale)

    def p_value(self, value: object) -> str:
        """A p-value: ``< 0,001`` instead of a long tail of zeros."""
        number = float(value)  # type: ignore[arg-type]
        return f"< {self.num(SMALL_P, 3)}" if number < SMALL_P else self.num(number, 3)

    def metric_text(self, metric_id: str, fallback: str, key: str = "label") -> str:
        """The label of a metric in the card's language (Uzbek text from the pack)."""
        metric = self.ctx.metrics.get(metric_id)
        if metric is None:
            return self.token(fallback)
        if self.locale == "uz":
            uz = metric.i18n.get("uz", {})
            return self.token(str(uz.get(key) or uz.get("label") or metric.label))
        return self.token(metric.label)

    def token(self, label: str) -> str:
        """A label with a technical term becomes a glossary token, ``[[key|visible text]]``."""
        terms = find_jargon(label, self.locale)
        if not terms:
            return label
        entry = find_entry(label.rstrip(" %"), self.locale) or find_entry(terms[0], self.locale)
        return f"[[{entry.key}|{label}]]" if entry else label

    def metric_gen(self, metric_id: str, fallback: str) -> str:
        """Genitive/linking form of the label (``daromadning``), or the lower-case English label."""
        metric = self.ctx.metrics.get(metric_id)
        if metric is not None and self.locale == "uz":
            gen = metric.i18n.get("uz", {}).get("label_gen")
            if gen:
                return str(gen)
        return self.metric_text(metric_id, fallback).lower()


def fact(finding: Finding, key: str, default: object = 0.0) -> object:
    """A fact of the finding."""
    return finding.facts.get(key, default)


def relative_change(finding: Finding) -> float:
    """Percent change of a trend in relative terms (also for metrics that are themselves %)."""
    facts = finding.facts
    if "change_pct" in facts:
        return float(facts["change_pct"])
    last, previous = float(facts["last_value"]), float(facts["previous_value"])
    return (last - previous) / abs(previous) * 100 if previous else float(facts["change_pp"])


def trend_values(f: Finding, x: Fmt) -> Values:
    unit = str(fact(f, "unit", "count"))
    return {
        "metric": x.metric_text(f.metric_id, str(fact(f, "metric_label", f.metric_id))),
        "pct": x.absnum(relative_change(f)),
        "compare": compare_text("prev_month", x.locale),
        "previous": x.value(fact(f, "previous_value"), unit),
        "current": x.value(fact(f, "last_value"), unit),
    }


def anomaly_values(f: Finding, x: Fmt) -> Values:
    unit = str(fact(f, "unit", "count"))
    value = float(fact(f, "value"))  # type: ignore[arg-type]
    typical = fact(f, "expected_value", None)
    if typical is None and fact(f, "ratio_x", None):
        typical = value / float(fact(f, "ratio_x"))  # type: ignore[arg-type]
    return {
        "date": x.day(fact(f, "date")),
        "metric": x.metric_text(f.metric_id, str(fact(f, "metric_label", f.metric_id))),
        "value": x.value(value, unit),
        "typical": x.value(typical if typical is not None else value, unit),
        "z": x.absnum(fact(f, "z_score")),
        "n": x.num(fact(f, "rows_that_day"), 0),
    }


def concentration_values(f: Finding, x: Fmt) -> Values:
    label = str(fact(f, "metric_label", f.metric_id))
    return {
        "metric_short": x.metric_text(f.metric_id, label, "short"),
        "metric_gen": x.metric_gen(f.metric_id, label),
        "share": x.num(fact(f, "top1_share_pct"), 0),
        "segment": fact(f, "top_segment", ""),
        "top3": x.num(fact(f, "top3_share_pct"), 0),
    }


def driver_values(f: Finding, x: Fmt) -> Values:
    unit = str(fact(f, "unit", "count"))
    return {
        "metric": x.metric_text(f.metric_id, str(fact(f, "metric_label", f.metric_id))),
        "share": x.num(fact(f, "share_of_change_pct"), 0),
        "segment": fact(f, "segment", ""),
        "change": x.signed(float(fact(f, "total_change")), unit),  # type: ignore[arg-type]
    }


def segment_gap_values(f: Finding, x: Fmt) -> Values:
    unit = str(fact(f, "unit", "count"))
    return {
        "best": fact(f, "high_segment", ""),
        "worst": fact(f, "low_segment", ""),
        "metric": x.metric_text(f.metric_id, str(fact(f, "metric_label", f.metric_id))),
        "ratio": x.num(fact(f, "ratio_x")),
        "best_value": x.value(fact(f, "high_value"), unit),
        "worst_value": x.value(fact(f, "low_value"), unit),
    }


def momentum_values(f: Finding, x: Fmt) -> Values:
    unit = str(fact(f, "unit", "count"))
    return {
        "segment": fact(f, "segment", ""),
        "metric": x.metric_text(f.metric_id, str(fact(f, "metric_label", f.metric_id))),
        "pct": x.absnum(fact(f, "growth_pct")),
        "compare": compare_text("prev_year", x.locale),
        "previous": x.value(fact(f, "previous_value"), unit),
        "current": x.value(fact(f, "current_value"), unit),
    }


def churn_values(f: Finding, x: Fmt) -> Values:
    customers = fact(f, "customers", None)
    return {
        "rate": x.num(fact(f, "churn_pct")),
        "baseline": x.num(fact(f, "baseline_churn_pct")),
        "customers": x.num(customers, 0) if customers is not None else EMPTY,
    }


def data_quality_values(f: Finding, x: Fmt) -> Values:
    rows = float(fact(f, "rows")) * float(fact(f, "null_pct")) / 100  # type: ignore[arg-type]
    return {
        "column": fact(f, "column", ""),
        "pct": x.num(fact(f, "null_pct"), 0),
        "rows": x.num(rows, 0),
    }


def funnel_values(f: Finding, x: Fmt) -> Values:
    return {
        "stage_from": fact(f, "stage_from", ""),
        "stage_to": fact(f, "stage_to", ""),
        "rate": x.num(fact(f, "rate_pct"), 0),
        "typical": x.num(fact(f, "typical_pct"), 0),
        "entered": x.num(fact(f, "entered"), 0),
    }


def stalled_values(f: Finding, x: Fmt) -> Values:
    return {
        "share": x.num(fact(f, "share_pct"), 0),
        "days": x.num(fact(f, "days"), 0),
        "deals": x.num(fact(f, "deals"), 0),
        "value": x.value(fact(f, "value"), "currency"),
    }


def win_rate_gap_values(f: Finding, x: Fmt) -> Values:
    return {
        "best": fact(f, "best", ""),
        "worst": fact(f, "worst", ""),
        "ratio": x.num(fact(f, "ratio")),
        "n_best": x.num(fact(f, "n_best"), 0),
        "n_worst": x.num(fact(f, "n_worst"), 0),
    }


def cycle_values(f: Finding, x: Fmt) -> Values:
    return {
        "pct": x.absnum(fact(f, "change_pct"), 0),
        "compare": compare_text(str(fact(f, "compare_key", "prev_period")), x.locale),
        "days": x.num(fact(f, "days"), 0),
        "previous_days": x.num(fact(f, "previous_days"), 0),
    }


def outliers_values(f: Finding, x: Fmt) -> Values:
    return {
        "column": fact(f, "column", ""),
        "count": x.num(fact(f, "count"), 0),
        "share": x.num(fact(f, "share_pct")),
        "extreme": x.num(fact(f, "extreme"), 2),
        "iqr_factor": x.num(fact(f, "iqr_factor"), 0),
    }


def correlation_values(f: Finding, x: Fmt) -> Values:
    return {
        "a": fact(f, "a", ""),
        "b": fact(f, "b", ""),
        "rho": x.num(fact(f, "rho"), 2),
        "n": x.num(fact(f, "n"), 0),
    }


def group_difference_values(f: Finding, x: Fmt) -> Values:
    return {
        "metric": fact(f, "metric", ""),
        "high": fact(f, "high", ""),
        "low": fact(f, "low", ""),
        "pct": x.num(fact(f, "pct"), 0),
        "test": TEST_NAMES[x.locale].get(str(fact(f, "test", "")), str(fact(f, "test", ""))),
        "effect": x.num(fact(f, "effect"), 2),
    }


def skewness_values(f: Finding, x: Fmt) -> Values:
    return {
        "column": fact(f, "column", ""),
        "skew": x.num(fact(f, "skew"), 2),
        "n": x.num(fact(f, "n"), 0),
    }


def missing_values(f: Finding, x: Fmt) -> Values:
    return {
        "column": fact(f, "column", ""),
        "category": fact(f, "category", ""),
        "pct": x.num(fact(f, "pct"), 0),
        "v": x.num(fact(f, "v"), 2),
        "p": x.p_value(fact(f, "p_adj", 0.0)),
    }


def regression_values(f: Finding, x: Fmt) -> Values:
    return {
        "column": fact(f, "column", ""),
        "change": x.absnum(fact(f, "change"), 2),
        "r2": x.num(fact(f, "r2"), 2),
        "p": x.p_value(fact(f, "p_adj", 0.0)),
    }


MAPPERS: dict[str, Callable[[Finding, Fmt], Values]] = {
    "trend": trend_values,
    "anomaly": anomaly_values,
    "concentration": concentration_values,
    "driver": driver_values,
    "segment_gap": segment_gap_values,
    "segment_momentum": momentum_values,
    "churn": churn_values,
    "data_quality": data_quality_values,
    "funnel_drop": funnel_values,
    "stalled": stalled_values,
    "win_rate_gap": win_rate_gap_values,
    "cycle_change": cycle_values,
    "outliers": outliers_values,
    "correlation": correlation_values,
    "group_difference": group_difference_values,
    "skewness": skewness_values,
    "missing_pattern": missing_values,
    "trend_regression": regression_values,
}


def direction_for(finding: Finding) -> str:
    """The frame direction of a finding; types without a direction use ``neutral``."""
    allowed = FINDING_TYPES[finding.type].directions
    if allowed == ("neutral",):
        return "neutral"
    direction = str(finding.facts.get("direction", ""))
    if direction in allowed:
        return direction
    raise FrameError(f"{finding.type} needs a direction of {allowed}")


PERIOD_WORDS = {
    "uz": {
        "all deals": "barcha bitimlar",
        "all closed deals": "yopilgan bitimlar",
        "open deals today": "hozirgi ochiq bitimlar",
    },
    "en": {},
}
SYNTHETIC_LABELS = {"uz": {"Stage conversion": "Bosqichdan bosqichga o\u02bbtish"}, "en": {}}


def period_chip(label: str, locale: str) -> str:
    """A period label in the card's language: ``2026-09 / 2026-08``, ``2026-07 - 2026-09``."""
    words = PERIOD_WORDS[locale]
    if label in words:
        return words[label]
    for separator in (" vs ", " to ", ".."):
        if separator in label:
            first, second = label.split(separator, 1)
            return f"{first} / {second}" if separator == " vs " else f"{first} \u2013 {second}"
    return label


def evidence_chips(finding: Finding, x: Fmt) -> list[str]:
    """Short facts under the headline: metric, period, group."""
    chips = []
    label = str(fact(finding, "metric_label", ""))
    if label and finding.type != "data_quality":
        label = SYNTHETIC_LABELS[x.locale].get(label, label)
        chips.append(x.metric_text(finding.metric_id, label))
    if finding.period_label and finding.period_label != "all data in view":
        chips.append(period_chip(finding.period_label, x.locale))
    chips += [str(v) for v in (finding.segment or {}).values()]
    return chips


def explain(finding: Finding, ctx: PlainContext, level: str = "simple") -> PlainCard:
    """The card of one finding in the language and at the level asked for."""
    if finding.type not in MAPPERS:
        raise FrameError(f"no wording for finding type {finding.type!r}")
    fmt = Fmt(ctx)
    direction = direction_for(finding)
    text = render_card(
        finding.type, direction, level, ctx.locale, MAPPERS[finding.type](finding, fmt)
    )
    return PlainCard(
        finding_id=finding.id,
        type=finding.type,
        direction=direction,
        level=level,
        locale=ctx.locale,
        headline=text.headline,
        meaning=text.meaning,
        next_step=text.next_step,
        details=text.details,
        evidence=evidence_chips(finding, fmt),
        priority=priority_level(finding),
        confidence=confidence_level(finding),
        assessment=str(finding.facts.get("assessment", "neutral")),
    )


def plain_text(text: str) -> str:
    """The text as a person reads it: glossary tokens ``[[key|word]]`` become ``word``."""
    return TOKEN_RE.sub(lambda match: match.group(2), text)


def jargon_in(card: PlainCard) -> list[str]:
    """Banned technical terms in the card's simple-level texts (empty = clean)."""
    if card.level == "expert":
        return []
    found: list[str] = []
    for text in (card.headline, card.meaning, card.next_step):
        found += find_jargon(text, card.locale)
    return found
