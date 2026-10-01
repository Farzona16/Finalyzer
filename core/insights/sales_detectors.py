"""Sales detectors: funnel_drop, stalled, win_rate_gap and cycle_change (spec section 8).

A deal's ``status`` is open, won or lost; its ``stage`` is where it is (open), where it
stopped (lost) or "Won". Rates always use closed deals only, so deals that are still open
never look like losses. Sales outcomes are placed on the day a deal *closed* when a trend is
needed, because by creation month the recent months are always unfinished (see ``cycle_change``).
"""

from __future__ import annotations

import statistics
from datetime import date

from core.ingest.profiler import quote_identifier
from core.insights.context import (
    InsightContext,
    add_months,
    as_date,
    dimensions,
    last_complete_month,
    month_end,
    month_label,
)
from core.insights.ranking import clamp01, confidence
from core.insights.text import humanize
from core.metrics.timeintel import date_column, date_literal
from core.models import Finding

CLOSE_COLUMN = "close_date"
MIN_CLOSED_PER_GROUP = 30
WIN_RATE_RATIO = 2.0
FUNNEL_MIN_ENTERED = 100
FUNNEL_GAP_POINTS = 20.0
STALLED_DAYS = 90
STALLED_SHARE = 0.15
STALLED_MIN_OPEN = 20
CYCLE_CHANGE = 0.15
CYCLE_MIN_DEALS = 30
CYCLE_MONTHS = 3
WON = ("won", "closed won", "closed_won")
LOST = ("lost", "closed lost", "closed_lost")
OPEN = ("open",)
# Where each known stage name sits in the funnel (English, Uzbek and Russian spellings).
STAGE_ORDER = {
    "lead": 0,
    "new": 0,
    "lid": 0,
    "yangi": 0,
    "qualified": 1,
    "qualification": 1,
    "saralangan": 1,
    "proposal": 2,
    "quote": 2,
    "taklif": 2,
    "negotiation": 3,
    "negotiating": 3,
    "muzokara": 3,
    "won": 4,
    "closed won": 4,
    "closed_won": 4,
    "yutildi": 4,
}
WON_POSITION = 4
NOT_GROUPS = ("reason", "stage", "status")


def in_list(values: tuple[str, ...]) -> str:
    """A SQL list of string literals."""
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def status_is(ctx: InsightContext, values: tuple[str, ...]) -> str:
    """SQL condition: the status column holds one of ``values`` (any letter case)."""
    column = quote_identifier(ctx.roles["status"])
    return f"LOWER(CAST({column} AS VARCHAR)) IN ({in_list(values)})"


def has_columns(ctx: InsightContext, *roles: str) -> bool:
    """True when every role is mapped."""
    return all(role in ctx.roles for role in roles)


def has_literal_column(ctx: InsightContext, name: str) -> bool:
    """True when the flat view has a column with exactly this name."""
    return name in {c.name for c in ctx.model.columns}


def month_start_of(label: str) -> date:
    """``2026-09`` -> first day of that month."""
    year, month = label.split("-")
    return date(int(year), int(month), 1)


def closed_counts(ctx: InsightContext, start: date, end: date) -> tuple[int, int]:
    """``(won, closed)`` deals created in the date range."""
    day = date_column(ctx.roles)
    sql = (
        f"SELECT COUNT(*) FILTER (WHERE {status_is(ctx, WON)}), "
        f"COUNT(*) FILTER (WHERE {status_is(ctx, WON)} OR {status_is(ctx, LOST)}) FROM data "
        f"WHERE {day} >= {date_literal(start)} AND {day} <= {date_literal(end)}"
    )
    won, closed = ctx.rows(sql)[0]
    return int(won), int(closed)


# ---------------------------------------------------------------------- win_rate_gap


def win_rates(ctx: InsightContext, dimension: str) -> dict[str, tuple[float, int]]:
    """Win rate (0..1) and number of closed deals for each category of ``dimension``."""
    column = quote_identifier(dimension)
    sql = (
        f"SELECT {column}, COUNT(*) FILTER (WHERE {status_is(ctx, WON)}), "
        f"COUNT(*) FILTER (WHERE {status_is(ctx, LOST)}) "
        f"FROM data WHERE {column} IS NOT NULL GROUP BY 1"
    )
    result = {}
    for name, won, lost in ctx.rows(sql):
        closed = int(won) + int(lost)
        if closed >= MIN_CLOSED_PER_GROUP:
            result[str(name)] = (int(won) / closed, closed)
    return result


def win_rate_dimensions(ctx: InsightContext) -> list[str]:
    """Columns to compare win rates by: owner and source first, outcome columns never."""
    names = [ctx.roles[r] for r in ("owner", "source") if r in ctx.roles]
    names += [d for d in dimensions(ctx) if d not in names]
    return [n for n in names if not any(word in n.lower() for word in NOT_GROUPS)]


def build_win_rate_gap(dimension: str, rates: dict[str, tuple[float, int]]) -> Finding | None:
    """A finding when the best group wins at least twice as often as the worst."""
    if len(rates) < 2:
        return None
    best = max(rates, key=lambda k: rates[k][0])
    worst = min(rates, key=lambda k: rates[k][0])
    low = rates[worst][0]
    ratio = rates[best][0] / low if low > 0 else float("inf")
    if ratio < WIN_RATE_RATIO:
        return None
    ratio = min(ratio, 99.0)
    facts: dict[str, float | str] = {
        "dimension": dimension,
        "best": best,
        "worst": worst,
        "best_pct": round(rates[best][0] * 100, 1),
        "worst_pct": round(low * 100, 1),
        "ratio": round(ratio, 2),
        "n_best": float(rates[best][1]),
        "n_worst": float(rates[worst][1]),
        "metric_label": "Win rate",
        "unit": "percent",
    }
    support = min(rates[best][1], rates[worst][1])
    return Finding(
        id=f"win_rate_gap:{dimension}",
        type="win_rate_gap",
        metric_id="win_rate_pct",
        title=f"Win rate: {best} wins {ratio:.1f}x as often as {worst} by {humanize(dimension)}",
        facts=facts,
        segment={dimension: worst},
        period_label="all closed deals",
        support=support,
        severity=round(clamp01((ratio - 1) / 3), 4),
        confidence=confidence(None, support),
    )


def detect_win_rate_gap(ctx: InsightContext) -> list[Finding]:
    """Win-rate differences between owners, sources, products and so on."""
    if not has_columns(ctx, "status"):
        return []
    findings = []
    for dimension in win_rate_dimensions(ctx):
        found = build_win_rate_gap(dimension, win_rates(ctx, dimension))
        if found:
            findings.append(found)
    return findings


# ---------------------------------------------------------------------- funnel_drop


def stage_counts(ctx: InsightContext) -> dict[int, tuple[int, str]]:
    """Deals per funnel position: ``{position: (deals, stage name)}`` for known stage names."""
    column = quote_identifier(ctx.roles["stage"])
    closed = (
        f" AND ({status_is(ctx, WON)} OR {status_is(ctx, LOST)})" if "status" in ctx.roles else ""
    )
    rows = ctx.rows(
        f"SELECT {column}, COUNT(*) FROM data WHERE {column} IS NOT NULL{closed} GROUP BY 1"
    )
    counts: dict[int, tuple[int, str]] = {}
    for name, count in rows:
        position = STAGE_ORDER.get(str(name).strip().lower())
        if position is not None:
            old = counts.get(position, (0, str(name)))
            counts[position] = (old[0] + int(count), old[1])
    return counts


def conversions(counts: dict[int, tuple[int, str]]) -> list[tuple[str, str, float, int]]:
    """``(from, to, rate, entered)`` for consecutive stages before "Won"; a deal that reached
    a later stage also passed the earlier ones. Only closed deals are counted, because an open
    deal has not had its chance to move on yet."""
    positions = sorted(counts)
    reached = {p: sum(counts[q][0] for q in positions if q >= p) for p in positions}
    pairs = []
    for current, following in zip(positions, positions[1:], strict=False):
        if following >= WON_POSITION:
            continue  # the last step is a win rate, not a funnel step
        if reached[current] > 0:
            pairs.append(
                (
                    counts[current][1],
                    counts[following][1],
                    reached[following] / reached[current],
                    reached[current],
                )
            )
    return pairs


def detect_funnel_drop(ctx: InsightContext) -> list[Finding]:
    """The stage step whose conversion is 20 points below the median of the other steps."""
    if not has_columns(ctx, "stage"):
        return []
    pairs = conversions(stage_counts(ctx))
    if len(pairs) < 3:
        ctx.note("Funnel check skipped: fewer than four known stages before Won were found.")
        return []
    worst = min(pairs, key=lambda p: p[2])
    others = [p[2] for p in pairs if p is not worst]
    typical = statistics.median(others)
    gap = (typical - worst[2]) * 100
    if worst[3] < FUNNEL_MIN_ENTERED or gap < FUNNEL_GAP_POINTS:
        return []
    start, end, rate, entered = worst
    return [
        Finding(
            id=f"funnel_drop:{start}:{end}",
            type="funnel_drop",
            metric_id="stage_conversion",
            title=f"Only {rate * 100:.0f}% of deals move from {start} to {end}",
            facts={
                "stage_from": start,
                "stage_to": end,
                "rate_pct": round(rate * 100, 1),
                "typical_pct": round(typical * 100, 1),
                "gap_points": round(gap, 1),
                "entered": float(entered),
                "metric_label": "Stage conversion",
                "unit": "percent",
            },
            segment={"stage": start},
            period_label="all deals",
            support=entered,
            severity=round(clamp01(gap / 50), 4),
            confidence=confidence(None, entered),
        )
    ]


# ---------------------------------------------------------------------- stalled


def detect_stalled(ctx: InsightContext) -> list[Finding]:
    """Open deals older than 90 days when they hold at least 15% of the open pipeline value."""
    if not has_columns(ctx, "status", "date", "amount"):
        return []
    day = date_column(ctx.roles)
    amount = quote_identifier(ctx.roles["amount"])
    latest = f"(SELECT MAX(CAST({day} AS DATE)) FROM data)"
    old = f"date_diff('day', CAST({day} AS DATE), {latest}) > {STALLED_DAYS}"
    sql = (
        f"SELECT COUNT(*) FILTER (WHERE {old}), COALESCE(SUM({amount}) FILTER (WHERE {old}), 0), "
        f"COUNT(*), COALESCE(SUM({amount}), 0) FROM data WHERE {status_is(ctx, OPEN)}"
    )
    stalled_n, stalled_value, open_n, open_value = ctx.rows(sql)[0]
    if int(open_n) < STALLED_MIN_OPEN or not open_value:
        return []
    share = float(stalled_value) / float(open_value)
    if share < STALLED_SHARE:
        return []
    return [
        Finding(
            id="stalled:open_deals",
            type="stalled",
            metric_id="stalled_deals",
            title=f"{share * 100:.0f}% of the open pipeline is older than {STALLED_DAYS} days",
            facts={
                "share_pct": round(share * 100, 1),
                "days": float(STALLED_DAYS),
                "deals": float(stalled_n),
                "value": round(float(stalled_value), 0),
                "open_deals": float(open_n),
                "open_value": round(float(open_value), 0),
                "metric_label": "Stalled deals",
                "unit": "currency",
            },
            period_label="open deals today",
            support=int(stalled_n),
            severity=round(clamp01(share / 0.5), 4),
            confidence=confidence(None, int(stalled_n)),
        )
    ]


# ---------------------------------------------------------------------- cycle_change


def cycle_window(ctx: InsightContext, start: date, end: date) -> tuple[float | None, int]:
    """Average days from creation to close (and deal count) for deals closed in the range."""
    day, close = date_column(ctx.roles), quote_identifier(CLOSE_COLUMN)
    sql = (
        f"SELECT AVG(date_diff('day', CAST({day} AS DATE), CAST({close} AS DATE))), COUNT(*) "
        f"FROM data WHERE {close} IS NOT NULL AND CAST({close} AS DATE) >= {date_literal(start)} "
        f"AND CAST({close} AS DATE) <= {date_literal(end)}"
    )
    value, count = ctx.rows(sql)[0]
    return (None if value is None else float(value)), int(count)


def close_bounds(ctx: InsightContext) -> tuple[date, date] | None:
    """First and last close date."""
    close = quote_identifier(CLOSE_COLUMN)
    low, high = ctx.rows(f"SELECT MIN({close}), MAX({close}) FROM data")[0]
    if low is None or high is None:
        return None
    return as_date(low), as_date(high)


def detect_cycle_change(ctx: InsightContext) -> list[Finding]:
    """Average cycle of deals closed in the last 3 months against the 3 months before."""
    if not has_columns(ctx, "date") or not has_literal_column(ctx, CLOSE_COLUMN):
        return []
    bounds = close_bounds(ctx)
    if bounds is None:
        return []
    last = last_complete_month(bounds)
    recent_start, prior_end = add_months(last, 1 - CYCLE_MONTHS), add_months(last, -CYCLE_MONTHS)
    now, n_now = cycle_window(ctx, recent_start, month_end(last))
    before, n_before = cycle_window(
        ctx, add_months(prior_end, 1 - CYCLE_MONTHS), month_end(prior_end)
    )
    if now is None or not before or min(n_now, n_before) < CYCLE_MIN_DEALS:
        return []
    change = (now - before) / before
    if abs(change) < CYCLE_CHANGE:
        return []
    direction = "up" if change > 0 else "down"
    return [
        Finding(
            id="cycle_change:avg_cycle_days",
            type="cycle_change",
            metric_id="avg_cycle_days",
            title=f"The sales cycle {'lengthened' if change > 0 else 'shortened'} "
            f"{abs(change) * 100:.0f}% in {month_label(recent_start)}..{month_label(last)}",
            facts={
                "days": round(now, 1),
                "previous_days": round(before, 1),
                "change_pct": round(change * 100, 1),
                "direction": direction,
                "assessment": "bad" if change > 0 else "good",
                "compare_key": "prev_quarter",
                "last_period": month_label(last),
                "previous_period": month_label(prior_end),
                "deals": float(n_now),
                "metric_label": "Average sales cycle (days)",
                "unit": "count",
            },
            period_label=f"{month_label(recent_start)}..{month_label(last)}",
            support=min(n_now, n_before),
            severity=round(clamp01(abs(change) / 0.5), 4),
            confidence=confidence(CYCLE_MONTHS * 2, min(n_now, n_before)),
        )
    ]
