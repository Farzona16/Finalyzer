"""Detectors that work on time: ``trend``, ``anomaly`` and ``churn``."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta

from core.ingest.profiler import quote_identifier
from core.insights.context import (
    MIN_MONTHS,
    InsightContext,
    add_months,
    as_date,
    daily_series,
    last_complete_month,
    month_end,
    month_label,
    monthly_series,
    total_in_range,
)
from core.insights.ranking import clamp01, confidence
from core.insights.text import assessment, direction_of, pct, verb
from core.metrics.timeintel import date_column, date_literal
from core.models import Finding, Metric

MIN_TREND_PCT = 0.10
TREND_STD_MULTIPLE = 1.5
MIN_TREND_PP = 2.0
MIN_ANOMALY_DAYS = 60
ANOMALY_WINDOW_DAYS = 90
ANOMALY_Z = 3.5
MAX_ANOMALIES = 3
CHURN_BASELINE_MONTHS = 6
MIN_CHURN_HISTORY = 3
CHURN_FLAG_PP = 10.0


@dataclass
class TrendCheck:
    """Outcome of the trend test of one metric. ``change`` is a fraction (or points/100)."""

    finding: Finding | None
    change: float | None


def historical_changes(values: list[tuple[date, float]]) -> list[float]:
    """Month-over-month fractional changes between consecutive months of the history."""
    changes = []
    for (p1, v1), (p2, v2) in zip(values, values[1:], strict=False):
        if add_months(p1, 1) == p2 and v1 != 0:
            changes.append((v2 - v1) / abs(v1))
    return changes


def trend_threshold(history: list[float]) -> float:
    """``max(10%, 1.5 x std of past month-over-month changes)``."""
    spread = statistics.pstdev(history) if len(history) >= 2 else 0.0
    return max(MIN_TREND_PCT, TREND_STD_MULTIPLE * spread)


def year_ago_change(values: list[tuple[date, float]]) -> float | None:
    """Percent change of the last month against the same month one year earlier."""
    last_period, last_value = values[-1]
    for period, value in values:
        if period == add_months(last_period, -12) and value != 0:
            return (last_value - value) / abs(value) * 100
    return None


def build_trend(
    ctx: InsightContext, metric: Metric, values: list[tuple[date, float]], change: float,
    threshold: float, incomplete: str | None,
) -> Finding:
    """Assemble the finding for a flagged trend."""
    (last_p, last_v), (prev_p, prev_v) = values[-1], values[-2]
    percent = metric.unit == "percent"
    direction = direction_of(change)
    facts: dict[str, float | str] = {
        "metric_label": metric.label,
        "unit": metric.unit,
        "last_period": month_label(last_p),
        "previous_period": month_label(prev_p),
        "last_value": last_v,
        "previous_value": prev_v,
        "direction": direction,
        "assessment": assessment(direction, metric.good_direction),
    }
    if percent:
        facts["change_pp"] = round(change, 2)
        title = (
            f"{metric.label} {verb(direction)} {abs(change):.1f} points in {month_label(last_p)}"
        )
        severity = clamp01(abs(change) / 10)
    else:
        facts["change_pct"] = round(change * 100, 2)
        facts["threshold_pct"] = round(threshold * 100, 2)
        title = f"{metric.label} {verb(direction)} {pct(change * 100)} in {month_label(last_p)}"
        severity = clamp01(abs(change) / 0.5)
    yoy = year_ago_change(values)
    if yoy is not None:
        facts["yoy_pct"] = round(yoy, 2)
    if incomplete:
        facts["incomplete_period"] = incomplete
    _, support = total_in_range(ctx, metric, last_p, month_end(last_p))
    return Finding(
        id=f"trend:{metric.id}", type="trend", metric_id=metric.id, title=title, facts=facts,
        period_label=f"{month_label(last_p)} vs {month_label(prev_p)}", support=support,
        severity=round(severity, 4), confidence=confidence(len(values), support),
    )


def check_trend(
    ctx: InsightContext, metric: Metric, upto: date, incomplete: str | None
) -> TrendCheck:
    """Test the last complete month against the previous one."""
    values = monthly_series(ctx, metric, upto)
    if len(values) < MIN_MONTHS:
        ctx.note(f"Trend for {metric.label} skipped: it needs at least 3 months of data.")
        return TrendCheck(None, None)
    if add_months(values[-2][0], 1) != values[-1][0]:
        ctx.note(f"Trend for {metric.label} skipped: the last two months are not consecutive.")
        return TrendCheck(None, None)
    (_, last_v), (_, prev_v) = values[-1], values[-2]
    if metric.unit == "percent":
        change = last_v - prev_v
        flagged, threshold = abs(change) >= MIN_TREND_PP, MIN_TREND_PP
    else:
        if prev_v == 0:
            return TrendCheck(None, None)
        change = (last_v - prev_v) / abs(prev_v)
        threshold = trend_threshold(historical_changes(values[:-1]))
        flagged = abs(change) >= threshold
    finding = build_trend(ctx, metric, values, change, threshold, incomplete) if flagged else None
    return TrendCheck(finding, change)


def detect_trends(ctx: InsightContext, bounds: tuple[date, date]) -> list[Finding]:
    """Trend findings for the primary metric and for other metrics that tell a separate story.

    Rate metrics (percent) are always tested. Other metrics are kept only when their change
    differs from the primary metric's change by at least 10 points, because they would
    otherwise repeat the same story.
    """
    upto = last_complete_month(bounds)
    incomplete = month_label(bounds[1]) if bounds[1].replace(day=1) > upto else None
    primary = check_trend(ctx, ctx.primary, upto, incomplete)
    findings = [primary.finding] if primary.finding else []
    for metric in ctx.metrics:
        if metric.id == ctx.primary.id:
            continue
        check = check_trend(ctx, metric, upto, incomplete)
        if check.finding is None:
            continue
        if metric.unit == "percent" or primary.change is None or (
            abs(check.change - primary.change) >= MIN_TREND_PCT
        ):
            findings.append(check.finding)
    return findings


def weekday_residuals(days: list[tuple[date, float]]) -> list[float]:
    """Each day's value minus the median of the same weekday."""
    by_weekday: dict[int, list[float]] = {}
    for day, value in days:
        by_weekday.setdefault(day.weekday(), []).append(value)
    medians = {k: statistics.median(v) for k, v in by_weekday.items()}
    return [value - medians[day.weekday()] for day, value in days]


def robust_z_scores(residuals: list[float]) -> list[float] | None:
    """``0.6745 x (x - median) / MAD``; None when the spread is zero."""
    centre = statistics.median(residuals)
    mad = statistics.median(abs(r - centre) for r in residuals)
    if mad == 0:
        return None
    return [0.6745 * (r - centre) / mad for r in residuals]


def build_anomaly(
    ctx: InsightContext, metric: Metric, day: date, value: float, z: float,
    residual: float, n_months: float,
) -> Finding:
    """Assemble the finding for one anomalous day."""
    expected = value - residual  # residual is measured from the typical weekday level
    day_column = date_column(ctx.roles)
    rows = ctx.rows(
        f"SELECT median(n) FROM (SELECT COUNT(*) AS n FROM data GROUP BY {day_column})"
    )
    on_day = ctx.rows(f"SELECT COUNT(*) FROM data WHERE {day_column} = {date_literal(day)}")
    support = int(rows[0][0] or 0)  # typical rows per day: an odd day may have few rows
    facts: dict[str, float | str] = {
        "metric_label": metric.label,
        "unit": metric.unit,
        "date": day.isoformat(),
        "value": value,
        "rows_that_day": float(on_day[0][0]),
        "z_score": round(z, 2),
        "direction": "up" if z > 0 else "down",
        "kind": "spike" if z > 0 else "drop",
    }
    if expected > 0 and value >= 0:
        facts["expected_value"] = round(expected, 2)
        facts["ratio_x"] = round(value / expected, 2)
    title = f"{metric.label} {'spiked' if z > 0 else 'dropped'} on {day.isoformat()}"
    return Finding(
        id=f"anomaly:{metric.id}:{day.isoformat()}", type="anomaly", metric_id=metric.id,
        title=title, facts=facts, period_label=day.isoformat(), support=support,
        severity=round(clamp01(abs(z) / 10), 4), confidence=confidence(n_months, support),
    )


def detect_anomalies(ctx: InsightContext, bounds: tuple[date, date]) -> list[Finding]:
    """Days in the last 90 days whose value is far outside the weekday pattern."""
    if (bounds[1] - bounds[0]).days + 1 < MIN_ANOMALY_DAYS:
        ctx.note("Anomaly check skipped: it needs at least 60 days of data.")
        return []
    days = daily_series(ctx, ctx.primary)
    if len(days) < MIN_ANOMALY_DAYS // 2:
        ctx.note("Anomaly check skipped: too few days with data.")
        return []
    residuals = weekday_residuals(days)
    z_scores = robust_z_scores(residuals)
    if z_scores is None:
        return []
    window_start = bounds[1] - timedelta(days=ANOMALY_WINDOW_DAYS - 1)
    centre = statistics.median(residuals)
    flagged = [
        (abs(z), i) for i, z in enumerate(z_scores)
        if abs(z) >= ANOMALY_Z and days[i][0] >= window_start
    ]
    n_months = (bounds[1] - bounds[0]).days / 30
    findings = []
    for _, i in sorted(flagged, reverse=True)[:MAX_ANOMALIES]:
        day, value = days[i]
        residual = residuals[i] - centre
        z = z_scores[i]
        findings.append(build_anomaly(ctx, ctx.primary, day, value, z, residual, n_months))
    return findings


CHURN_SQL = """
WITH m AS (
  SELECT DISTINCT {cust} AS c, date_trunc('month', {day}) AS mo FROM data
  WHERE {cust} IS NOT NULL AND {day} IS NOT NULL AND {day} <= {upto}
),
act AS (SELECT mo, COUNT(*) AS active FROM m GROUP BY mo),
ret AS (
  SELECT a.mo AS mo, COUNT(b.c) AS retained FROM m a
  LEFT JOIN m b ON b.c = a.c AND b.mo = a.mo + INTERVAL 1 MONTH GROUP BY a.mo
),
firsts AS (SELECT c, MIN(mo) AS fmo FROM m GROUP BY c),
newc AS (SELECT fmo AS mo, COUNT(*) AS new_customers FROM firsts GROUP BY fmo)
SELECT act.mo, act.active, ret.retained, COALESCE(newc.new_customers, 0)
FROM act JOIN ret ON ret.mo = act.mo LEFT JOIN newc ON newc.mo = act.mo ORDER BY act.mo
"""


def churn_rows(ctx: InsightContext, upto: date) -> list[tuple[date, int, int, int]]:
    """Per month: active customers, how many of them return next month, first-time customers."""
    sql = CHURN_SQL.format(
        cust=quote_identifier(ctx.roles["customer_id"]),
        day=date_column(ctx.roles),
        upto=date_literal(month_end(upto)),
    )
    return [(as_date(r[0]), int(r[1]), int(r[2]), int(r[3])) for r in ctx.rows(sql)]


def churn_series(rows: list[tuple[date, int, int, int]]) -> list[tuple[date, float, float, int]]:
    """``(month, churn rate, new customer share, active in previous month)`` per month."""
    by_month = {r[0]: r for r in rows}
    result = []
    for month, active, _, new in rows:
        previous = by_month.get(add_months(month, -1))
        if previous is None or previous[1] == 0 or active == 0:
            continue
        churn = 1 - previous[2] / previous[1]
        result.append((month, churn, new / active, previous[1]))
    return result


def detect_churn(ctx: InsightContext, bounds: tuple[date, date]) -> list[Finding]:
    """Compare the last complete month's churn with the mean of the six months before."""
    if "customer_id" not in ctx.roles or "date" not in ctx.roles:
        ctx.note("Churn skipped: it needs a customer id column and a date column.")
        return []
    upto = last_complete_month(bounds)
    series = churn_series(churn_rows(ctx, upto))
    if not series or series[-1][0] != upto or len(series) - 1 < MIN_CHURN_HISTORY:
        ctx.note("Churn skipped: it needs at least 3 earlier months of customer activity.")
        return []
    history = [c for _, c, _, _ in series[-1 - CHURN_BASELINE_MONTHS : -1]]
    month, churn, new_share, active_previous = series[-1]
    baseline = statistics.fmean(history)
    change_pp = (churn - baseline) * 100
    if abs(change_pp) < CHURN_FLAG_PP:
        return []
    direction = direction_of(change_pp)
    facts: dict[str, float | str] = {
        "metric_label": "Customer churn rate",
        "unit": "percent",
        "last_period": month_label(month),
        "churn_pct": round(churn * 100, 2),
        "baseline_churn_pct": round(baseline * 100, 2),
        "change_pp": round(change_pp, 2),
        "new_customer_share_pct": round(new_share * 100, 2),
        "direction": direction,
        "assessment": assessment(direction, "down"),
    }
    title = (
        f"Customer churn {'rose' if direction == 'up' else 'fell'} to "
        f"{churn * 100:.1f}% in {month_label(month)}"
    )
    return [
        Finding(
            id="churn:customers", type="churn", metric_id="churn_rate", title=title, facts=facts,
            period_label=f"{month_label(month)} vs the previous {len(history)} months",
            support=active_previous, severity=round(clamp01(abs(change_pp) / 20), 4),
            confidence=confidence(len(series), active_previous),
        )
    ]
