"""Offline metric candidates (spec 6.1, step 7).

No model and no key are needed: from the column profile alone this module writes metric
objects in the restricted language of ``dsl.py`` (sums and averages of numeric columns, shares
of status-like values, days between date columns, distinct counts of identifiers, missing
rates). They go through the same validation as every other metric.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

from core.autopilot.dsl import Context, is_id_like
from core.models import ColumnInfo

TEMPLATES_FILE = Path(__file__).parent / "labels.json"
MONEY_ROLES = ("amount", "cost", "target")
MAX_NUMERIC_COLUMNS = 8
FULL_STATS_COLUMNS = 4
MAX_SHARE_COLUMNS = 3
MAX_SHARE_VALUES = 3
MAX_DURATIONS = 2
MAX_MISSING = 2
MAX_CATEGORY_COUNT = 6
POSITIVE_WORDS = frozenset(
    "yes true won completed paid success successful passed pass ok active done ha yutildi "
    "yakunlangan toʻlangan да выиграно".split()
)
NEGATIVE_WORDS = frozenset(
    "no false lost failed refunded cancelled canceled churned left attrition rejected reversed "
    "returned yoʻq yutqazildi muvaffaqiyatsiz нет проиграно".split()
)
PASS_NAME_WORDS = ("pass", "success", "ok", "oʻtdi", "годен")


@cache
def label_templates() -> dict[str, dict[str, Any]]:
    """The label templates (English and Uzbek) by key."""
    return json.loads(TEMPLATES_FILE.read_text(encoding="utf-8"))["templates"]


def human(name: str) -> str:
    """A column name as words: ``close_date`` -> ``close date``."""
    return name.replace("_", " ").strip()


def make_label(key: str, **params: str) -> dict[str, str]:
    """Fill a label template in both languages."""
    template = label_templates()[key]
    values = {k: human(v) if k in ("col", "a", "b") else v for k, v in params.items()}
    return {lang: template[lang].format(**values) for lang in ("en", "uz")}


def slug(*parts: str) -> str:
    """A metric id made of ASCII letters, digits and underscores (at most 64 characters)."""
    text = "_".join(parts).lower()
    cleaned = "".join(ch if ch.isascii() and ch.isalnum() else "_" for ch in text)
    return ("m_" + "_".join(p for p in cleaned.split("_") if p))[:64]


def numeric_columns(ctx: Context) -> list[ColumnInfo]:
    """Usable numeric columns, the most informative first."""
    found = [
        c
        for c in ctx.columns.values()
        if c.dtype in ("integer", "float")
        and not c.is_pii
        and not is_id_like(c, ctx)
        and c.distinct >= 2
        and c.null_pct <= 50
    ]
    money = set(ctx.roles.get(r) for r in MONEY_ROLES)
    found.sort(key=lambda c: (c.name not in money, -min(c.distinct, 50), c.null_pct))
    return found[:MAX_NUMERIC_COLUMNS]


def simple(
    agg: str,
    column: str,
    label: dict[str, str],
    *,
    unit: str,
    priority: int,
    name: str,
    q: float | None = None,
    direction: str = "neutral",
) -> dict[str, Any]:
    """One single-column metric object."""
    raw: dict[str, Any] = {
        "id": slug(name, column),
        "agg": agg,
        "columns": [column],
        "unit": unit,
        "good_direction": direction,
        "label": label,
        "priority": priority,
    }
    if q is not None:
        raw["q"] = q
    return raw


def numeric_candidates(ctx: Context, statistics: bool) -> list[dict[str, Any]]:
    """Averages (and sums of money columns); the full descriptive set for statistics data."""
    out: list[dict[str, Any]] = []
    money = {ctx.roles.get(r): r for r in MONEY_ROLES}
    for rank, info in enumerate(numeric_columns(ctx)):
        name = info.name
        unit = "currency" if name in money else "ratio"
        if name in money:
            label = make_label("sum", col=name)
            out.append(simple("sum", name, label, unit=unit, priority=85, name="sum"))
        average = (80 if statistics else 70) if name in money or statistics else 60
        out.append(
            simple(
                "avg",
                name,
                make_label("avg", col=name),
                unit=unit,
                priority=average - 3 * rank,
                name="avg",
            )
        )
        if rank < FULL_STATS_COLUMNS or statistics:
            out += stats_candidates(name, unit, rank, 72 if statistics else 50)
    return out


def stats_candidates(column: str, unit: str, rank: int, start: int) -> list[dict[str, Any]]:
    """Median, spread, extremes and quartiles of one column."""
    base = start - 3 * rank
    specs = (
        ("median", "median", None, base),
        ("stddev", "std", None, base - 5),
        ("min", "min", None, base - 15),
        ("max", "max", None, base - 15),
        ("quantile", "p25", 0.25, base - 18),
        ("quantile", "p75", 0.75, base - 18),
    )
    return [
        simple(agg, column, make_label(key, col=column), unit=unit, priority=prio, name=key, q=q)
        for agg, key, q, prio in specs
    ]


def identifier_candidates(ctx: Context) -> list[dict[str, Any]]:
    """Distinct counts of the order and customer identifiers."""
    out = []
    for role, priority in (("customer_id", 65), ("order_id", 60)):
        column = ctx.roles.get(role)
        if column:
            out.append(
                simple(
                    "count_distinct",
                    column,
                    make_label("distinct", col=column),
                    unit="count",
                    priority=priority,
                    name="distinct",
                    direction="up",
                )
            )
    return out


def share_direction(value: str) -> str:
    """``up`` for good-sounding values, ``down`` for bad-sounding ones, else ``neutral``."""
    word = value.strip().casefold()
    if word in POSITIVE_WORDS:
        return "up"
    return "down" if word in NEGATIVE_WORDS else "neutral"


def share_metric(column: str, value: str, priority: int) -> dict[str, Any]:
    """Percent of rows where ``column`` equals ``value``."""
    condition = {"column": column, "op": "=", "value": value}
    return {
        "id": slug("share", column, value),
        "agg": "ratio",
        "times100": True,
        "numerator": {"agg": "count", "filter": [condition]},
        "denominator": {"agg": "count"},
        "unit": "percent",
        "good_direction": share_direction(value),
        "priority": priority,
        "label": make_label("share", col=column, value=value),
    }


def pick_share_values(info: ColumnInfo) -> list[str]:
    """Which values of a category column deserve a share metric."""
    values = list(info.categories or [])
    if len(values) == 2:
        positive = [v for v in values if share_direction(v) != "neutral"]
        return positive[:1] or values[1:]
    return values[:MAX_SHARE_VALUES]


def share_candidates(ctx: Context, statistics: bool = False) -> list[dict[str, Any]]:
    """Shares of the values of status-like columns (few categories)."""
    status = {ctx.roles.get("status"), ctx.roles.get("stage")}
    columns = [
        c
        for c in ctx.columns.values()
        if c.dtype == "text"
        and not c.is_pii
        and c.categories
        and 2 <= len(c.categories) <= MAX_CATEGORY_COUNT
        and c.null_pct <= 50
    ]
    columns.sort(key=lambda c: (c.name not in status, len(c.categories or [])))
    out: list[dict[str, Any]] = []
    for rank, info in enumerate(columns[:MAX_SHARE_COLUMNS]):
        base = (48 if statistics else 72) - 6 * rank
        out += [share_metric(info.name, v, base - i) for i, v in enumerate(pick_share_values(info))]
    return out


def duration_candidates(ctx: Context) -> list[dict[str, Any]]:
    """Average days between date columns (first date column to each later one)."""
    dates = [c.name for c in ctx.columns.values() if c.dtype == "date" and not c.is_pii]
    out = []
    first = ctx.columns[dates[0]] if dates else None
    for later in dates[1 : 1 + MAX_DURATIONS]:
        if first and str(ctx.columns[later].min_value) < str(first.min_value):
            continue
        out.append(
            {
                "id": slug("days", dates[0], later),
                "agg": "avg_days_between",
                "columns": [dates[0], later],
                "unit": "count",
                "good_direction": "down",
                "priority": 68,
                "label": make_label("days_between", a=dates[0], b=later),
            }
        )
    return out


def missing_candidates(ctx: Context) -> list[dict[str, Any]]:
    """Share of empty cells of the emptiest columns (1% to 100%)."""
    gappy = sorted(
        (c for c in ctx.columns.values() if c.null_pct >= 1 and not c.is_pii),
        key=lambda c: -c.null_pct,
    )
    return [
        {
            "id": slug("missing", c.name),
            "agg": "ratio",
            "times100": True,
            "numerator": {"agg": "count", "filter": [{"column": c.name, "op": "is_null"}]},
            "denominator": {"agg": "count"},
            "unit": "percent",
            "good_direction": "down",
            "priority": 35,
            "label": make_label("missing", col=c.name),
        }
        for c in gappy[:MAX_MISSING]
    ]


def boolean_candidates(ctx: Context) -> list[dict[str, Any]]:
    """Share of true values of yes/no columns."""
    out = []
    for info in ctx.columns.values():
        if info.dtype != "boolean" or info.is_pii:
            continue
        passing = any(w in info.name.casefold() for w in PASS_NAME_WORDS)
        condition = {"column": info.name, "op": "=", "value": True}
        out.append(
            {
                "id": slug("rate", info.name),
                "agg": "ratio",
                "times100": True,
                "numerator": {"agg": "count", "filter": [condition]},
                "denominator": {"agg": "count"},
                "unit": "percent",
                "good_direction": "up" if passing else "neutral",
                "priority": 80 if passing else 55,
                "label": make_label("pass_rate" if passing else "rate", col=info.name),
            }
        )
    return out


def heuristic_candidates(ctx: Context, statistics: bool = False) -> list[dict[str, Any]]:
    """All offline candidates for a dataset (to be parsed and validated by the caller)."""
    rows = {
        "id": "m_rows",
        "agg": "count",
        "unit": "count",
        "good_direction": "neutral",
        "priority": 55,
        "label": make_label("rows"),
    }
    return [
        rows,
        *numeric_candidates(ctx, statistics),
        *identifier_candidates(ctx),
        *share_candidates(ctx, statistics),
        *boolean_candidates(ctx),
        *duration_candidates(ctx),
        *missing_candidates(ctx),
    ]
