"""Data behind the dashboard charts. Charts are drawn from templates, never from LLM code.

Each function returns a small pandas DataFrame; ``app/ui_dashboard.py`` turns it into a figure.
"""

from __future__ import annotations

from datetime import date

import duckdb
import pandas as pd

from core.config import Settings, get_settings
from core.dashboard.filters import choose_slicers, find_column
from core.errors import ValidationError
from core.ingest.profiler import quote_identifier
from core.metrics.builder import metric_sql
from core.metrics.timeintel import choose_grain, date_column, series, top_n, yoy
from core.models import DatasetModel, Metric
from core.query.executor import run_with_timeout

TOP_N = 10
BREAKDOWN_LIMIT = 15
PREVIEW_ROWS = 200
MIN_MONTHS_FOR_OVERLAY = 13
DIMENSION_OPTIONS = 50


def query_frame(con: duckdb.DuckDBPyConnection, sql: str, settings: Settings) -> pd.DataFrame:
    """Run an internally built query with the time limit and return a DataFrame."""
    columns, rows = run_with_timeout(con, sql, settings.query_timeout_s)
    return pd.DataFrame(rows, columns=columns)


def data_span(con: duckdb.DuckDBPyConnection, model: DatasetModel) -> tuple[date, date] | None:
    """First and last date in the filtered ``data`` (None without a date column or rows)."""
    if "date" not in model.roles:
        return None
    column = date_column(model.roles)
    low, high = con.execute(f"SELECT MIN({column}), MAX({column}) FROM data").fetchone()
    return (low, high) if low is not None and high is not None else None


def months_between(start: date, end: date) -> int:
    """Number of calendar months touched by the span."""
    return (end.year - start.year) * 12 + end.month - start.month + 1


def can_overlay_previous_year(con: duckdb.DuckDBPyConnection, model: DatasetModel) -> bool:
    """True when the filtered data spans at least 13 months."""
    span = data_span(con, model)
    return span is not None and months_between(*span) >= MIN_MONTHS_FOR_OVERLAY


def trend_frame(
    con: duckdb.DuckDBPyConnection,
    model: DatasetModel,
    metric: Metric,
    overlay_previous_year: bool = False,
    settings: Settings | None = None,
) -> pd.DataFrame:
    """Metric per period at an automatic grain; optionally with last year's value alongside.

    Columns: ``period``, ``value`` and (with the overlay) ``previous_year_value``.
    """
    settings = settings or get_settings()
    span = data_span(con, model)
    if span is None:
        return pd.DataFrame(columns=["period", "value"])
    grain = choose_grain(*span)
    if overlay_previous_year:
        sql = yoy(metric, model.roles, grain)
        frame = query_frame(con, sql, settings).drop(columns=["yoy_pct"])
    else:
        frame = query_frame(con, series(metric, model.roles, grain), settings)
    frame.attrs["grain"] = grain
    return frame


def last_period_is_partial(frame: pd.DataFrame, last_date: date) -> bool:
    """True when the final period of a trend extends past the last date in the data."""
    grain = frame.attrs.get("grain")
    if frame.empty or grain in (None, "day"):
        return False
    start = pd.Timestamp(frame["period"].iloc[-1])
    step = {"week": pd.DateOffset(weeks=1), "month": pd.DateOffset(months=1)}.get(grain)
    if step is None:
        return False
    return (start + step - pd.Timedelta(days=1)).date() > last_date


def dimension_options(model: DatasetModel) -> list[str]:
    """Columns the breakdown can be split by (low-cardinality text columns)."""
    return choose_slicers(model, limit=DIMENSION_OPTIONS)


def breakdown_frame(
    con: duckdb.DuckDBPyConnection,
    model: DatasetModel,
    metric: Metric,
    dimension: str,
    limit: int = BREAKDOWN_LIMIT,
    settings: Settings | None = None,
) -> pd.DataFrame:
    """Top categories of a dimension by the metric: ``category``, ``value``, ``share_pct``.

    ``share_pct`` is filled only for additive metrics (a share of an average is meaningless).
    """
    settings = settings or get_settings()
    if find_column(model, dimension).dtype != "text":
        raise ValidationError(f"Column '{dimension}' cannot be used as a breakdown.")
    frame = query_frame(con, top_n(metric, model.roles, dimension, limit), settings)
    if not metric.additive:
        frame["share_pct"] = None
    return frame


def top_table(
    con: duckdb.DuckDBPyConnection,
    model: DatasetModel,
    metric: Metric,
    dimension: str,
    settings: Settings | None = None,
) -> pd.DataFrame:
    """The Top-10 table with share of total."""
    return breakdown_frame(con, model, metric, dimension, TOP_N, settings)


def variation_sql(metric: Metric, roles: dict[str, str], dimension: str) -> str:
    """Coefficient of variation of the metric across the categories of a dimension."""
    dim = quote_identifier(dimension)
    return (
        f"WITH g AS (SELECT {metric_sql(metric, roles)} AS v FROM data "
        f"WHERE {dim} IS NOT NULL GROUP BY {dim}) "
        "SELECT COUNT(*), stddev_pop(v) / NULLIF(ABS(AVG(v)), 0) FROM g"
    )


def default_dimension(
    con: duckdb.DuckDBPyConnection,
    model: DatasetModel,
    metric: Metric,
    settings: Settings | None = None,
) -> str | None:
    """The dimension whose categories differ most on the metric (highest variation)."""
    settings = settings or get_settings()
    best: tuple[float, str] | None = None
    for name in choose_slicers(model, limit=6):
        _, rows = run_with_timeout(
            con, variation_sql(metric, model.roles, name), settings.query_timeout_s
        )
        categories, variation = rows[0]
        if categories and categories >= 2 and variation is not None:
            if best is None or variation > best[0]:
                best = (float(variation), name)
    if best:
        return best[1]
    options = dimension_options(model)
    return options[0] if options else None


def preview_frame(con: duckdb.DuckDBPyConnection, rows: int = PREVIEW_ROWS) -> pd.DataFrame:
    """First rows of the filtered data (personal data columns are not part of ``data``)."""
    return con.execute(f"SELECT * FROM data LIMIT {int(rows)}").df()
