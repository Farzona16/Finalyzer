"""Detectors that compare categories: concentration, driver, segment gap and momentum."""

from __future__ import annotations

import math
from datetime import date

from core.insights.context import (
    SMALL_SUPPORT,
    InsightContext,
    add_months,
    dimensions,
    grouped,
    last_complete_month,
    month_end,
    month_label,
    total_in_range,
)
from core.insights.ranking import clamp01, confidence
from core.insights.text import assessment, humanize, pct
from core.models import Finding, Metric

MIN_CATEGORIES = 5
MIN_CATEGORIES_STRONG = 3  # with so few categories, only a dominant one is notable
STRONG_TOP1_SHARE = 0.50
TOP1_SHARE = 0.30
TOP3_SHARE = 0.60
DRIVER_SHARE = 0.40
DRIVER_LEVEL_MULTIPLE = 1.5
MAX_DRIVERS_PER_TREND = 2
GAP_RATIO = 2.0
GAP_POINTS = 5.0
MOMENTUM_GAP = 0.25
MOMENTUM_MIN_MONTHS = 15
MAX_MOMENTUM_PER_DIMENSION = 2


def metric_by_id(ctx: InsightContext, metric_id: str) -> Metric | None:
    """Look a metric up among the available ones."""
    return next((m for m in ctx.metrics if m.id == metric_id), None)


def parse_label(label: str) -> date:
    """``2026-09`` -> first day of that month."""
    year, month = label.split("-")
    return date(int(year), int(month), 1)


def detect_concentration(ctx: InsightContext) -> list[Finding]:
    """Dimensions where one category (>= 30%) or three categories (>= 60%) hold most of it."""
    metric = ctx.primary
    if not metric.additive:
        ctx.note("Concentration skipped: the main metric cannot be split into shares.")
        return []
    findings = []
    for dimension in dimensions(ctx):
        values = {k: v for k, (v, _) in grouped(ctx, metric, dimension).items() if v and v > 0}
        if len(values) < MIN_CATEGORIES_STRONG:
            continue
        ranked = sorted(values.items(), key=lambda item: -item[1])
        total = sum(values.values())
        top1, top3 = ranked[0][1] / total, sum(v for _, v in ranked[:3]) / total
        if len(values) < MIN_CATEGORIES:
            if top1 < STRONG_TOP1_SHARE:
                continue
            top3 = top1  # three categories always hold 100%; that says nothing
        elif top1 < TOP1_SHARE and top3 < TOP3_SHARE:
            continue
        findings.append(build_concentration(ctx, metric, dimension, ranked, total, top1, top3))
    return findings


def build_concentration(
    ctx: InsightContext, metric: Metric, dimension: str, ranked: list[tuple[str, float]],
    total: float, top1: float, top3: float,
) -> Finding:
    """Assemble one concentration finding."""
    name = ranked[0][0]
    hhi = sum((v / total * 100) ** 2 for _, v in ranked)
    support = grouped(ctx, metric, dimension)[name][1]
    facts: dict[str, float | str] = {
        "metric_label": metric.label,
        "unit": metric.unit,
        "dimension": dimension,
        "top_segment": name,
        "top1_share_pct": round(top1 * 100, 2),
        "top3_share_pct": round(top3 * 100, 2),
        "categories": float(len(ranked)),
        "hhi": round(hhi, 0),
    }
    if top1 >= TOP1_SHARE:
        title = f"{name} accounts for {top1 * 100:.0f}% of {metric.label.lower()}"
    else:
        title = (
            f"The top 3 by {humanize(dimension)} hold {top3 * 100:.0f}% of {metric.label.lower()}"
        )
    return Finding(
        id=f"concentration:{metric.id}:{dimension}", type="concentration", metric_id=metric.id,
        title=title,
        facts=facts, segment={dimension: name}, period_label="all data in view", support=support,
        severity=round(clamp01(top1), 4), confidence=confidence(None, support),
    )


def driver_for(
    ctx: InsightContext, trend: Finding, metric: Metric, dimension: str
) -> Finding | None:
    """The category that explains the trend's change in one dimension, if any."""
    facts = trend.facts
    last_p = parse_label(str(facts["last_period"]))
    prev_p = parse_label(str(facts["previous_period"]))
    total_change = float(facts["last_value"]) - float(facts["previous_value"])
    if total_change == 0:
        return None
    now = grouped(ctx, metric, dimension, last_p, month_end(last_p))
    before = grouped(ctx, metric, dimension, prev_p, month_end(prev_p))
    prior_total = sum(v for v, _ in before.values() if v) or 0.0
    rows = []
    for name in set(now) | set(before):
        delta = (now.get(name, (0, 0))[0] or 0.0) - (before.get(name, (0, 0))[0] or 0.0)
        level = ((before.get(name, (0, 0))[0] or 0.0) / prior_total) if prior_total else 0.0
        support = max(now.get(name, (0, 0))[1], before.get(name, (0, 0))[1])
        rows.append((name, delta, delta / total_change, level, support))
    rows.sort(key=lambda r: -abs(r[1]))
    name, delta, share, level, support = rows[0]
    if share < DRIVER_SHARE or share < DRIVER_LEVEL_MULTIPLE * level:
        return None
    return build_driver(trend, metric, dimension, rows, (name, delta, share, level, support))


def build_driver(
    trend: Finding, metric: Metric, dimension: str,
    rows: list[tuple[str, float, float, float, int]],
    top: tuple[str, float, float, float, int],
) -> Finding:
    """Assemble one driver finding from the ranked category contributions."""
    name, delta, share, level, support = top
    total_change = float(trend.facts["last_value"]) - float(trend.facts["previous_value"])
    facts: dict[str, float | str] = {
        "metric_label": metric.label,
        "unit": metric.unit,
        "dimension": dimension,
        "segment": name,
        "segment_change": round(delta, 2),
        "total_change": round(total_change, 2),
        "share_of_change_pct": round(share * 100, 1),
        "segment_level_share_pct": round(level * 100, 1),
        "categories": float(len(rows)),
        "direction": str(trend.facts["direction"]),
        "assessment": str(trend.facts["assessment"]),
        "last_period": str(trend.facts["last_period"]),
        "previous_period": str(trend.facts["previous_period"]),
    }
    if len(rows) > 1:
        second = rows[1]
        facts.update(
            second_segment=second[0], second_segment_change=round(second[1], 2),
            second_share_of_change_pct=round(second[2] * 100, 1), second_support=float(second[4]),
        )
    what = "rise" if trend.facts["direction"] == "up" else "drop"
    return Finding(
        id=f"driver:{metric.id}:{dimension}", type="driver", metric_id=metric.id,
        title=f"{name} explains {share * 100:.0f}% of the {metric.label.lower()} {what}",
        facts=facts, segment={dimension: name}, period_label=trend.period_label, support=support,
        severity=round(trend.severity * min(1.0, share), 4),
        confidence=confidence(None, support),
    )


def driver_order(finding: Finding) -> tuple[float, float]:
    """Sort key: biggest explained share first (capped at 100%), then the finer dimension."""
    share = min(1.0, float(finding.facts["share_of_change_pct"]) / 100)
    return -share, -float(finding.facts["categories"])


def detect_drivers(ctx: InsightContext, trends: list[Finding]) -> list[Finding]:
    """For each flagged trend of an additive metric: which categories explain the change."""
    findings = []
    for trend in trends:
        metric = metric_by_id(ctx, trend.metric_id)
        if trend.type != "trend" or metric is None or not metric.additive:
            continue
        found = [d for dim in dimensions(ctx) if (d := driver_for(ctx, trend, metric, dim))]
        found.sort(key=driver_order)
        findings += found[:MAX_DRIVERS_PER_TREND]
    return findings


def qualified(groups: dict[str, tuple[float | None, int]]) -> dict[str, tuple[float, int]]:
    """Categories with a value and at least 100 rows."""
    return {
        k: (v, n) for k, (v, n) in groups.items() if v is not None and n >= SMALL_SUPPORT
    }


def gap_severity(ratio: float | None, diff: float) -> float:
    """``min(1, log2(ratio)/3)``, or a points-based value when the low side is zero."""
    if ratio is not None:
        return clamp01(math.log2(ratio) / 3)
    return clamp01(diff / 20)


def gap_for(ctx: InsightContext, metric: Metric, dimension: str) -> Finding | None:
    """Best versus worst category of a rate or average metric within one dimension."""
    groups = qualified(grouped(ctx, metric, dimension))
    if len(groups) < 2:
        return None
    high = max(groups.items(), key=lambda item: item[1][0])
    low = min(groups.items(), key=lambda item: item[1][0])
    (high_name, (high_v, high_n)), (low_name, (low_v, low_n)) = high, low
    ratio = high_v / low_v if low_v > 0 and high_v > 0 else None
    diff = high_v - low_v
    percent = metric.unit == "percent"
    flagged = (ratio is not None and ratio >= GAP_RATIO) or (percent and diff >= GAP_POINTS)
    if not flagged:
        return None
    facts: dict[str, float | str] = {
        "metric_label": metric.label, "unit": metric.unit, "dimension": dimension,
        "high_segment": high_name, "low_segment": low_name,
        "high_value": high_v, "low_value": low_v,
        "high_support": float(high_n), "low_support": float(low_n),
    }
    facts["high_is"] = {"up": "better", "down": "worse"}.get(metric.good_direction, "different")
    if ratio is not None:
        facts["ratio_x"] = round(ratio, 2)
    if percent:
        facts["diff_pp"] = round(diff, 2)
    return Finding(
        id=f"segment_gap:{metric.id}:{dimension}", type="segment_gap", metric_id=metric.id,
        title=f"{metric.label} differs by {humanize(dimension)}: {high_name} vs {low_name}",
        facts=facts, segment={dimension: high_name}, period_label="all data in view",
        support=min(high_n, low_n), severity=round(gap_severity(ratio, diff), 4),
        confidence=confidence(None, min(high_n, low_n)),
    )


def detect_segment_gaps(ctx: InsightContext) -> list[Finding]:
    """Gap findings for every average or rate metric and dimension."""
    metrics = [
        m for m in ctx.metrics if not m.additive and m.unit in ("currency", "percent", "ratio")
    ]
    found = [gap_for(ctx, m, d) for m in metrics for d in dimensions(ctx)]
    return [f for f in found if f is not None]


def momentum_windows(bounds: tuple[date, date]) -> tuple[date, date, date, date] | None:
    """Last three complete months and the same months a year earlier (None if too short)."""
    last = last_complete_month(bounds)
    if add_months(bounds[0].replace(day=1), MOMENTUM_MIN_MONTHS - 1) > last:
        return None
    start = add_months(last, -2)
    return start, month_end(last), add_months(start, -12), month_end(add_months(last, -12))


def growth(current: float | None, previous: float | None) -> float | None:
    """Fractional growth, or None when the base is not positive."""
    if current is None or previous is None or previous <= 0:
        return None
    return current / previous - 1


def momentum_for(
    ctx: InsightContext, metric: Metric, dimension: str,
    windows: tuple[date, date, date, date], overall: float,
) -> list[Finding]:
    """Categories that grow much faster or slower than the whole over the last 3 months."""
    start, end, prev_start, prev_end = windows
    now = grouped(ctx, metric, dimension, start, end)
    before = grouped(ctx, metric, dimension, prev_start, prev_end)
    rows = []
    for name, (value, count) in now.items():
        g = growth(value, before.get(name, (None, 0))[0])
        if g is not None and count >= SMALL_SUPPORT and abs(g - overall) >= MOMENTUM_GAP:
            rows.append((abs(g - overall), name, g, value, before[name][0], count))
    rows.sort(reverse=True)
    return [
        build_momentum(metric, dimension, windows, overall, row) for row in
        rows[:MAX_MOMENTUM_PER_DIMENSION]
    ]


def build_momentum(
    metric: Metric, dimension: str, windows: tuple[date, date, date, date], overall: float,
    row: tuple[float, str, float, float, float, int],
) -> Finding:
    """Assemble one momentum finding."""
    gap, name, g, value, previous, count = row
    direction = "up" if g > overall else "down"
    facts: dict[str, float | str] = {
        "metric_label": metric.label, "unit": metric.unit, "dimension": dimension,
        "segment": name, "growth_pct": round(g * 100, 1),
        "overall_growth_pct": round(overall * 100, 1),
        "gap_pp": round((g - overall) * 100, 1),
        "current_value": value, "previous_value": previous,
        "window": f"{month_label(windows[0])} to {month_label(windows[1])}",
        "direction": direction, "assessment": assessment(direction, metric.good_direction),
    }
    return Finding(
        id=f"segment_momentum:{metric.id}:{dimension}:{name}", type="segment_momentum",
        metric_id=metric.id,
        title=f"{name} grew {pct(g * 100, 0)} year on year vs {pct(overall * 100, 0)} overall"
        if g > 0 else f"{name} changed {g * 100:.0f}% year on year vs {overall * 100:.0f}% overall",
        facts=facts, segment={dimension: name}, period_label=str(facts["window"]),
        support=count, severity=round(clamp01(gap / 0.5), 4), confidence=confidence(None, count),
    )


def detect_segment_momentum(ctx: InsightContext, bounds: tuple[date, date]) -> list[Finding]:
    """Year-on-year growth of the last three months per category, against the overall growth."""
    metric = ctx.primary
    windows = momentum_windows(bounds)
    if windows is None or not metric.additive:
        ctx.note("Segment momentum skipped: it needs at least 15 months of data.")
        return []
    start, end, prev_start, prev_end = windows
    current_total = total_in_range(ctx, metric, start, end)[0]
    previous_total = total_in_range(ctx, metric, prev_start, prev_end)[0]
    overall = growth(current_total, previous_total)
    if overall is None:
        return []
    findings: list[Finding] = []
    for dimension in dimensions(ctx):
        findings += momentum_for(ctx, metric, dimension, windows, overall)
    return findings
