"""Shared inputs and helpers for the detectors: connection, metrics and period arithmetic."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import duckdb

from core.config import Settings
from core.dashboard.chartspecs import dimension_options
from core.ingest.profiler import quote_identifier
from core.metrics.builder import metric_sql
from core.metrics.timeintel import date_column, date_literal, series
from core.models import DatasetModel, Metric
from core.query.executor import run_with_timeout

MIN_MONTHS = 3
SMALL_SUPPORT = 100


@dataclass
class InsightContext:
    """Everything a detector needs. ``notes`` collects reasons for skipped detectors."""

    con: duckdb.DuckDBPyConnection
    model: DatasetModel
    metrics: list[Metric]
    primary: Metric
    settings: Settings
    notes: list[str] = field(default_factory=list)

    @property
    def roles(self) -> dict[str, str]:
        """Role to column mapping of the dataset."""
        return self.model.roles

    def rows(self, sql: str) -> list[tuple[Any, ...]]:
        """Run an internally built query with the time limit."""
        return run_with_timeout(self.con, sql, self.settings.query_timeout_s)[1]

    def note(self, text: str) -> None:
        """Remember why a detector did not run."""
        if text not in self.notes:
            self.notes.append(text)


def month_start(value: date) -> date:
    """First day of the month of ``value``."""
    return value.replace(day=1)


def add_months(value: date, months: int) -> date:
    """First day of the month that is ``months`` after the month of ``value``."""
    index = value.year * 12 + value.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def month_end(value: date) -> date:
    """Last day of the month of ``value``."""
    return add_months(value, 1) - timedelta(days=1)


def month_label(value: date) -> str:
    """``2026-09`` style label."""
    return f"{value.year:04d}-{value.month:02d}"


def data_bounds(ctx: InsightContext) -> tuple[date, date] | None:
    """First and last date of the filtered data, or None when there is no date column/rows."""
    if "date" not in ctx.roles:
        return None
    column = date_column(ctx.roles)
    low, high = ctx.rows(f"SELECT MIN({column}), MAX({column}) FROM data")[0]
    return (low, high) if low is not None and high is not None else None


def last_complete_month(bounds: tuple[date, date]) -> date:
    """The last month fully covered by the data (the final month only if it reaches month end)."""
    last = month_start(bounds[1])
    return last if bounds[1] >= month_end(last) else add_months(last, -1)


def as_date(value: date | datetime) -> date:
    """DuckDB returns timestamps for truncated dates; keep the date part only."""
    return value.date() if isinstance(value, datetime) else value


def monthly_series(ctx: InsightContext, metric: Metric, upto: date) -> list[tuple[date, float]]:
    """Metric per month up to and including the month ``upto``; empty months are dropped."""
    rows = ctx.rows(series(metric, ctx.roles, "month"))
    pairs = [(as_date(r[0]), r[1]) for r in rows if r[1] is not None]
    return [(period, float(value)) for period, value in pairs if period <= upto]


def daily_series(ctx: InsightContext, metric: Metric) -> list[tuple[date, float]]:
    """Metric per day."""
    rows = ctx.rows(series(metric, ctx.roles, "day"))
    return [(as_date(r[0]), float(r[1])) for r in rows if r[1] is not None]


def dimensions(ctx: InsightContext) -> list[str]:
    """Low-cardinality text columns worth splitting by (the status column is excluded)."""
    status = ctx.roles.get("status")
    return [d for d in dimension_options(ctx.model) if d != status]


def grouped(
    ctx: InsightContext, metric: Metric, dimension: str, start: date | None = None,
    end: date | None = None,
) -> dict[str, tuple[float | None, int]]:
    """Metric value and row count per category, optionally inside a date range."""
    dim = quote_identifier(dimension)
    conditions = [f"{dim} IS NOT NULL"]
    if start is not None and end is not None:
        day = date_column(ctx.roles)
        conditions.append(f"{day} >= {date_literal(start)} AND {day} <= {date_literal(end)}")
    sql = (
        f"SELECT {dim}, {metric_sql(metric, ctx.roles)}, COUNT(*) FROM data "
        f"WHERE {' AND '.join(conditions)} GROUP BY 1"
    )
    return {
        str(r[0]): (None if r[1] is None else float(r[1]), int(r[2])) for r in ctx.rows(sql)
    }


def total_in_range(ctx: InsightContext, metric: Metric, start: date, end: date) -> tuple[
    float | None, int
]:
    """Metric value and row count over a date range."""
    day = date_column(ctx.roles)
    sql = (
        f"SELECT {metric_sql(metric, ctx.roles)}, COUNT(*) FROM data "
        f"WHERE {day} >= {date_literal(start)} AND {day} <= {date_literal(end)}"
    )
    value, count = ctx.rows(sql)[0]
    return (None if value is None else float(value)), int(count)
