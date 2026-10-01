"""Time intelligence: SQL builders for trends, period comparisons, YoY, YTD, shares and top-N.

All functions return DuckDB SQL text. Metric expressions come from the registry and column
names are quoted, so no user text is put into the SQL unescaped. Dates are formatted from
``datetime.date`` objects only.
"""

from __future__ import annotations

from datetime import date, timedelta

from core.errors import ValidationError
from core.ingest.profiler import quote_identifier
from core.metrics.builder import metric_sql
from core.models import Metric

GRAINS = ("day", "week", "month", "quarter")


def choose_grain(start: date, end: date) -> str:
    """Pick day, week or month from the length of the period."""
    span_days = (end - start).days + 1
    if span_days <= 62:
        return "day"
    if span_days <= 180:
        return "week"
    return "month"


def date_literal(value: date) -> str:
    """Format a date as a DuckDB literal."""
    return f"DATE '{date.fromisoformat(value.isoformat()).isoformat()}'"


def date_column(roles: dict[str, str]) -> str:
    """Quoted name of the date column; raises if no date role is mapped."""
    if "date" not in roles:
        raise ValidationError("This analysis needs a date column, but none is mapped.")
    return quote_identifier(roles["date"])


def check_grain(grain: str) -> str:
    """Return the grain if it is one of the supported values."""
    if grain not in GRAINS:
        raise ValidationError(f"Unknown time grain '{grain}'.")
    return grain


def series(metric: Metric, roles: dict[str, str], grain: str, source: str = "data") -> str:
    """Metric value per period, ordered by time."""
    day, expr = date_column(roles), metric_sql(metric, roles)
    return (
        f"SELECT date_trunc('{check_grain(grain)}', {day}) AS period, {expr} AS value "
        f"FROM {source} WHERE {day} IS NOT NULL GROUP BY 1 ORDER BY 1"
    )


def previous_range(start: date, end: date) -> tuple[date, date]:
    """The period of equal length that ends the day before ``start``."""
    length = (end - start).days + 1
    return start - timedelta(days=length), start - timedelta(days=1)


def period_compare(
    metric: Metric,
    roles: dict[str, str],
    start: date,
    end: date,
    source: str = "data",
    extra_where: str = "",
) -> str:
    """Current value vs the value of the immediately preceding period of equal length.

    ``extra_where`` is an already escaped condition (for example other slicers) or empty.
    """
    day, expr = date_column(roles), metric_sql(metric, roles)
    previous_start, previous_end = previous_range(start, end)
    extra = f" AND ({extra_where})" if extra_where else ""

    def part(first: date, last: date) -> str:
        return (
            f"(SELECT {expr} FROM {source} WHERE {day} >= {date_literal(first)} "
            f"AND {day} <= {date_literal(last)}{extra})"
        )

    return (
        f"SELECT {part(start, end)} AS current_value, "
        f"{part(previous_start, previous_end)} AS previous_value"
    )


def yoy(metric: Metric, roles: dict[str, str], grain: str, source: str = "data") -> str:
    """Metric per period next to the same period one year earlier."""
    shift = "INTERVAL 52 WEEK" if grain == "week" else "INTERVAL 1 YEAR"
    return (
        f"WITH s AS ({series(metric, roles, grain, source)}) "
        "SELECT cur.period AS period, cur.value AS value, prev.value AS previous_year_value, "
        "100.0 * (cur.value - prev.value) / NULLIF(prev.value, 0) AS yoy_pct "
        f"FROM s cur LEFT JOIN s prev ON prev.period = CAST(cur.period - {shift} AS DATE) "
        "ORDER BY cur.period"
    )


def ytd(metric: Metric, roles: dict[str, str], source: str = "data") -> str:
    """Year-to-date value at the latest date, and the same span one year earlier."""
    day, expr = date_column(roles), metric_sql(metric, roles)
    last = f"(SELECT MAX({day}) FROM {source})"
    year_start = f"date_trunc('year', {last})"
    return (
        f"SELECT (SELECT {expr} FROM {source} WHERE {day} >= {year_start} AND {day} <= {last}) "
        f"AS ytd_value, "
        f"(SELECT {expr} FROM {source} WHERE {day} >= {year_start} - INTERVAL 1 YEAR "
        f"AND {day} <= {last} - INTERVAL 1 YEAR) AS previous_ytd_value"
    )


def share_of_total(
    metric: Metric, roles: dict[str, str], dimension: str, source: str = "data"
) -> str:
    """Metric per category and each category's share of the total (for additive metrics)."""
    dim, expr = quote_identifier(dimension), metric_sql(metric, roles)
    return (
        f"WITH g AS (SELECT {dim} AS category, {expr} AS value FROM {source} "
        f"WHERE {dim} IS NOT NULL GROUP BY 1) "
        "SELECT category, value, 100.0 * value / NULLIF(SUM(value) OVER (), 0) AS share_pct "
        "FROM g ORDER BY value DESC NULLS LAST"
    )


def top_n(
    metric: Metric, roles: dict[str, str], dimension: str, n: int, source: str = "data"
) -> str:
    """The ``n`` categories with the highest metric value and their share of the total."""
    inner = share_of_total(metric, roles, dimension, source)
    return f"SELECT * FROM ({inner}) ORDER BY value DESC NULLS LAST LIMIT {int(n)}"
