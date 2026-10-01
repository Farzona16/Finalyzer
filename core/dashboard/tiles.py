"""Compile a resolved dashboard tile into query results (the LLM never writes this SQL)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import duckdb
import pandas as pd

from core.config import Settings
from core.dashboard.chartspecs import (
    breakdown_frame,
    query_frame,
    top_table,
    trend_frame,
)
from core.dashboard.filters import apply_filters
from core.errors import UserFacingError, ValidationError
from core.i18n.formats import format_compact, format_currency, format_decimal, format_integer
from core.ingest.profiler import quote_identifier
from core.insights.sales_detectors import STAGE_ORDER
from core.metrics.builder import metric_sql
from core.metrics.timeintel import date_column
from core.models import DatasetModel, FilterSpec, Metric
from core.templates.resolver import numeric_columns

BINS = 12
SAMPLE = 400


@dataclass
class TileData:
    """Neutral JSON for one chart or table."""

    tile_id: str
    type: str
    categories: list[str] = field(default_factory=list)
    series: list[dict[str, Any]] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    formatted_rows: list[list[str]] = field(default_factory=list)
    unit: str = ""
    sql: str | None = None
    cross_filter_column: str | None = None

    def as_dict(self, show_sql: bool) -> dict[str, Any]:
        payload = {
            "tile_id": self.tile_id,
            "type": self.type,
            "categories": self.categories,
            "series": _clean(self.series),
            "columns": self.columns,
            "rows": _clean(self.rows),
            "formatted_rows": self.formatted_rows,
            "unit": self.unit,
            "cross_filter_column": self.cross_filter_column,
            "source_sql": self.sql if show_sql else None,
        }
        return payload


def _clean(value: Any) -> Any:
    """JSON-friendly numbers and dates (numpy scalars become Python)."""
    if value is None:
        return None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, pd.Timestamp | date):
        return str(value)[:10]
    if hasattr(value, "item"):
        try:
            return _clean(value.item())
        except (ValueError, AttributeError):
            pass
    if isinstance(value, float) and pd.isna(value):
        return None
    return value


def format_number(value: object, unit: str, locale: str, currency: str) -> str:
    """One cell, using the shared formatters."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    if not isinstance(value, int | float) or isinstance(value, bool):
        return str(value)
    number = float(value)
    if unit == "currency":
        return format_currency(number, locale, currency)
    if unit == "percent":
        return format_decimal(number, locale, 1) + "%"
    if abs(number) >= 10_000:
        return format_compact(number, locale)
    if float(number).is_integer():
        return format_integer(int(number), locale)
    return format_decimal(number, locale, 1)


def metric_of(metrics: dict[str, Metric], tile: dict[str, Any]) -> Metric | None:
    """The first metric the tile asked for that exists."""
    for metric_id in tile.get("metric_ids") or []:
        if metric_id in metrics:
            return metrics[metric_id]
    return None


def restore_data(con: duckdb.DuckDBPyConnection) -> None:
    """Put the unfiltered flat view back (filters must not leak across requests)."""
    con.execute("CREATE OR REPLACE VIEW data AS SELECT * FROM base")


def series_from(
    categories: list[str],
    values: list[float | None],
    name: str,
    unit: str,
    locale: str,
    currency: str,
) -> list[dict[str, Any]]:
    """One named series with formatted labels."""
    shown = [format_number(v, unit, locale, currency) for v in values]
    return [{"name": name, "values": values, "formatted": shown}]


def from_breakdown(
    frame: pd.DataFrame, tile: dict[str, Any], metric: Metric, locale: str, currency: str
) -> TileData:
    """Donut, bar and funnel share this shape: category / value."""
    cats = [str(v) for v in frame["category"].tolist()] if not frame.empty else []
    vals = (
        [None if pd.isna(v) else float(v) for v in frame["value"].tolist()]
        if not frame.empty
        else []
    )
    data = TileData(
        tile_id=tile["id"],
        type=tile["type"],
        categories=cats,
        series=series_from(cats, vals, metric.label, metric.unit, locale, currency),
        columns=["category", "value"],
        rows=[[c, v] for c, v in zip(cats, vals, strict=True)],
        formatted_rows=[
            [c, format_number(v, metric.unit, locale, currency)]
            for c, v in zip(cats, vals, strict=True)
        ],
        unit=metric.unit,
        cross_filter_column=tile.get("dimension") if tile.get("cross_filter") else None,
    )
    return data


def query_scalar(
    con: duckdb.DuckDBPyConnection, metric: Metric, roles: dict[str, str]
) -> float | None:
    """One metric total on the current ``data`` view."""
    sql = f"SELECT {metric_sql(metric, roles)} FROM data"
    row = con.execute(sql).fetchone()
    if row is None or row[0] is None:
        return None
    return float(row[0])


def query_kpi(
    con: duckdb.DuckDBPyConnection,
    tile: dict[str, Any],
    metric: Metric,
    roles: dict[str, str],
    locale: str,
    currency: str,
) -> TileData:
    """A single number (KPI row cell, big number, gauge)."""
    value = query_scalar(con, metric, roles)
    shown = format_number(value, metric.unit, locale, currency)
    return TileData(
        tile_id=tile["id"],
        type=tile["type"],
        categories=[metric.label],
        series=series_from([metric.label], [value], metric.label, metric.unit, locale, currency),
        columns=["metric", "value"],
        rows=[[metric.label, value]],
        formatted_rows=[[metric.label, shown]],
        unit=metric.unit,
        sql=f"SELECT {metric_sql(metric, roles)} AS value FROM data",
    )


def query_trend(
    con: duckdb.DuckDBPyConnection,
    model: DatasetModel,
    tile: dict[str, Any],
    metric: Metric,
    settings: Settings,
    locale: str,
    currency: str,
) -> TileData:
    overlay = tile.get("compare") == "previous_year"
    frame = trend_frame(con, model, metric, overlay, settings)
    cats = [str(v)[:10] for v in frame["period"].tolist()] if not frame.empty else []
    vals = (
        [None if pd.isna(v) else float(v) for v in frame["value"].tolist()]
        if not frame.empty
        else []
    )
    series = series_from(cats, vals, metric.label, metric.unit, locale, currency)
    if overlay and "previous_year_value" in frame.columns:
        prev = [None if pd.isna(v) else float(v) for v in frame["previous_year_value"].tolist()]
        series += series_from(cats, prev, "previous_year", metric.unit, locale, currency)
    return TileData(
        tile_id=tile["id"],
        type=tile["type"],
        categories=cats,
        series=series,
        columns=list(frame.columns),
        rows=frame.astype(object).where(pd.notna(frame), None).values.tolist(),
        formatted_rows=[
            [str(r[0])[:10], format_number(r[1], metric.unit, locale, currency)]
            for r in frame.itertuples(index=False)
        ],
        unit=metric.unit,
    )


def query_stacked(
    con: duckdb.DuckDBPyConnection,
    model: DatasetModel,
    tile: dict[str, Any],
    metric: Metric,
    settings: Settings,
    locale: str,
    currency: str,
) -> TileData:
    dim = tile.get("dimension")
    if not dim or not isinstance(dim, str) or ":" in dim:
        return from_breakdown(
            pd.DataFrame(columns=["category", "value"]), tile, metric, locale, currency
        )
    day = date_column(model.roles)
    sql = (
        f"SELECT date_trunc('month', {day}) AS period, {quote_identifier(dim)} AS category, "
        f"{metric_sql(metric, model.roles)} AS value FROM data "
        f"WHERE {day} IS NOT NULL AND {quote_identifier(dim)} IS NOT NULL GROUP BY 1, 2 ORDER BY 1"
    )
    frame = query_frame(con, sql, settings)
    cats = sorted({str(v)[:10] for v in frame["period"].tolist()}) if not frame.empty else []
    groups = sorted({str(v) for v in frame["category"].tolist()}) if not frame.empty else []
    lookup = {
        (str(p)[:10], str(c)): float(v) for p, c, v in frame.itertuples(index=False) if pd.notna(v)
    }
    series = []
    for group in groups:
        values = [lookup.get((period, group)) for period in cats]
        series += series_from(cats, values, group, metric.unit, locale, currency)
    return TileData(
        tile_id=tile["id"],
        type=tile["type"],
        categories=cats,
        series=series,
        columns=["period", "category", "value"],
        rows=frame.astype(object).where(pd.notna(frame), None).values.tolist(),
        unit=metric.unit,
        sql=sql,
        cross_filter_column=dim if tile.get("cross_filter") else None,
    )


def query_heatmap_week(
    con: duckdb.DuckDBPyConnection,
    model: DatasetModel,
    tile: dict[str, Any],
    metric: Metric,
    settings: Settings,
    locale: str,
    currency: str,
) -> TileData:
    day = date_column(model.roles)
    sql = (
        f"SELECT CAST(strftime({day}, '%w') AS INTEGER) AS weekday, "
        f"date_trunc('week', {day}) AS week, {metric_sql(metric, model.roles)} AS value "
        f"FROM data WHERE {day} IS NOT NULL GROUP BY 1, 2 ORDER BY 2, 1"
    )
    frame = query_frame(con, sql, settings)
    weeks = [str(v)[:10] for v in frame["week"].unique().tolist()] if not frame.empty else []
    cats = [str(i) for i in range(7)]
    lookup = {
        (int(w), str(k)[:10]): float(v) for w, k, v in frame.itertuples(index=False) if pd.notna(v)
    }
    series = []
    for week in weeks:
        values = [lookup.get((int(d), week)) for d in range(7)]
        series += series_from(cats, values, week, metric.unit, locale, currency)
    return TileData(
        tile_id=tile["id"],
        type=tile["type"],
        categories=cats,
        series=series,
        columns=["weekday", "week", "value"],
        rows=frame.astype(object).where(pd.notna(frame), None).values.tolist(),
        unit=metric.unit,
        sql=sql,
    )


def query_histogram(
    con: duckdb.DuckDBPyConnection, tile: dict[str, Any], column: str, locale: str
) -> TileData:
    name = quote_identifier(column)
    frame = con.execute(f"SELECT {name} AS v FROM data WHERE {name} IS NOT NULL").df()
    if frame.empty or frame["v"].nunique() < 2:
        return TileData(tile_id=tile["id"], type=tile["type"], columns=["bin", "count"])
    counts, edges = pd.cut(frame["v"], bins=BINS, retbins=True, include_lowest=True)
    tallies = counts.value_counts(sort=False)
    cats = [f"{edges[i]:.2f}–{edges[i + 1]:.2f}" for i in range(len(edges) - 1)]
    vals = [int(n) for n in tallies.tolist()]
    return TileData(
        tile_id=tile["id"],
        type=tile["type"],
        categories=cats,
        series=series_from(cats, vals, column, "count", locale, "UZS"),
        columns=["bin", "count"],
        rows=[[c, n] for c, n in zip(cats, vals, strict=True)],
        formatted_rows=[[c, format_integer(n, locale)] for c, n in zip(cats, vals, strict=True)],
        unit="count",
    )


def query_boxplot(
    con: duckdb.DuckDBPyConnection, tile: dict[str, Any], column: str, locale: str
) -> TileData:
    name = quote_identifier(column)
    row = con.execute(
        f"SELECT min({name}), quantile_cont({name}, 0.25), median({name}), "
        f"quantile_cont({name}, 0.75), max({name}) FROM data WHERE {name} IS NOT NULL"
    ).fetchone()
    labels = ["min", "q1", "median", "q3", "max"]
    vals = [None if row is None or v is None else float(v) for v in (row or (None,) * 5)]
    return TileData(
        tile_id=tile["id"],
        type=tile["type"],
        categories=labels,
        series=series_from(labels, vals, column, "count", locale, "UZS"),
        columns=labels,
        rows=[vals],
        formatted_rows=[[format_number(v, "count", locale, "UZS") for v in vals]],
    )


def query_correlation(
    con: duckdb.DuckDBPyConnection, model: DatasetModel, tile: dict[str, Any], locale: str
) -> TileData:
    names = numeric_columns(model)[:8]
    if len(names) < 2:
        return TileData(tile_id=tile["id"], type=tile["type"])
    quoted = ", ".join(quote_identifier(n) for n in names)
    frame = con.execute(f"SELECT {quoted} FROM data").df()
    corr = frame.corr(numeric_only=True).fillna(0)
    series = [
        {
            "name": row,
            "values": [None if pd.isna(v) else round(float(v), 3) for v in corr.loc[row].tolist()],
            "formatted": [format_decimal(float(v), locale, 2) for v in corr.loc[row].tolist()],
        }
        for row in corr.index
    ]
    return TileData(
        tile_id=tile["id"],
        type=tile["type"],
        categories=list(corr.columns),
        series=series,
        columns=["a", "b", "r"],
        rows=[[a, b, round(float(corr.loc[a, b]), 3)] for a in corr.index for b in corr.columns],
        unit="ratio",
    )


def query_scatter(
    con: duckdb.DuckDBPyConnection, model: DatasetModel, tile: dict[str, Any], locale: str
) -> TileData:
    names = numeric_columns(model)[:2]
    if len(names) < 2:
        return TileData(tile_id=tile["id"], type=tile["type"])
    a, b = names[0], names[1]
    sql = (
        f"SELECT {quote_identifier(a)} AS x, {quote_identifier(b)} AS y FROM data "
        f"WHERE {quote_identifier(a)} IS NOT NULL AND {quote_identifier(b)} IS NOT NULL "
        f"LIMIT {SAMPLE}"
    )
    frame = con.execute(sql).df()
    xs = [None if pd.isna(v) else float(v) for v in frame["x"].tolist()]
    ys = [None if pd.isna(v) else float(v) for v in frame["y"].tolist()]
    return TileData(
        tile_id=tile["id"],
        type=tile["type"],
        categories=[a],
        series=[
            {
                "name": a,
                "values": xs,
                "formatted": [format_number(v, "count", locale, "UZS") for v in xs],
            },
            {
                "name": b,
                "values": ys,
                "formatted": [format_number(v, "count", locale, "UZS") for v in ys],
            },
        ],
        columns=[a, b],
        rows=[[x, y] for x, y in zip(xs, ys, strict=True)],
        unit="count",
    )


def query_describe(
    con: duckdb.DuckDBPyConnection, model: DatasetModel, tile: dict[str, Any], locale: str
) -> TileData:
    names = numeric_columns(model)
    if not names:
        return TileData(tile_id=tile["id"], type=tile["type"])
    quoted = ", ".join(quote_identifier(n) for n in names)
    frame = con.execute(f"SELECT {quoted} FROM data").df()
    desc = frame.describe().T.reset_index().rename(columns={"index": "column"})
    cols = [str(c) for c in desc.columns]
    rows = desc.astype(object).where(pd.notna(desc), None).values.tolist()
    shown = [
        [
            format_number(v, "count", locale, "UZS") if isinstance(v, int | float) else str(v)
            for v in row
        ]
        for row in rows
    ]
    return TileData(
        tile_id=tile["id"],
        type=tile["type"],
        columns=cols,
        rows=rows,
        formatted_rows=shown,
        categories=[str(r[0]) for r in rows],
    )


def query_funnel(
    con: duckdb.DuckDBPyConnection,
    tile: dict[str, Any],
    metric: Metric,
    model: DatasetModel,
    settings: Settings,
    locale: str,
    currency: str,
) -> TileData:
    dim = tile.get("dimension") or "stage"
    frame = breakdown_frame(con, model, metric, dim, 20, settings)
    if not frame.empty:
        frame["_rank"] = frame["category"].map(
            lambda v: STAGE_ORDER.get(str(v).strip().lower(), 99)
        )
        frame = frame.sort_values(["_rank", "value"], ascending=[True, False]).drop(columns="_rank")
    return from_breakdown(frame, tile, metric, locale, currency)


def query_bullet(
    con: duckdb.DuckDBPyConnection,
    tile: dict[str, Any],
    metric: Metric,
    model: DatasetModel,
    settings: Settings,
    locale: str,
    currency: str,
) -> TileData:
    dim = tile.get("dimension")
    if not dim or not isinstance(dim, str) or ":" in dim:
        return query_kpi(con, tile, metric, model.roles, locale, currency)
    frame = breakdown_frame(con, model, metric, dim, 12, settings)
    data = from_breakdown(frame, tile, metric, locale, currency)
    mean = float(frame["value"].mean()) if not frame.empty else None
    data.series.append(
        {
            "name": "average",
            "values": [mean] * len(data.categories),
            "formatted": [format_number(mean, metric.unit, locale, currency)]
            * len(data.categories),
        }
    )
    return data


def pick_numeric(tile: dict[str, Any], model: DatasetModel) -> str | None:
    """The numeric column a statistics tile should plot."""
    names = numeric_columns(model)
    return names[0] if names else None


def query_mean_over_time(
    con: duckdb.DuckDBPyConnection,
    model: DatasetModel,
    tile: dict[str, Any],
    settings: Settings,
    locale: str,
) -> TileData:
    """Monthly average of the most informative numeric column (statistics template)."""
    column = pick_numeric(tile, model)
    if column is None:
        return TileData(tile_id=tile["id"], type=tile["type"])
    day = date_column(model.roles)
    sql = (
        f"SELECT date_trunc('month', {day}) AS period, AVG({quote_identifier(column)}) AS value "
        f"FROM data WHERE {day} IS NOT NULL GROUP BY 1 ORDER BY 1"
    )
    frame = query_frame(con, sql, settings)
    cats = [str(v)[:10] for v in frame["period"].tolist()] if not frame.empty else []
    vals = (
        [None if pd.isna(v) else float(v) for v in frame["value"].tolist()]
        if not frame.empty
        else []
    )
    return TileData(
        tile_id=tile["id"],
        type=tile["type"],
        categories=cats,
        series=series_from(cats, vals, column, "count", locale, "UZS"),
        columns=["period", "value"],
        rows=[[c, v] for c, v in zip(cats, vals, strict=True)],
        unit="count",
        sql=sql,
    )


def query_group_means(
    con: duckdb.DuckDBPyConnection,
    model: DatasetModel,
    tile: dict[str, Any],
    settings: Settings,
    locale: str,
) -> TileData:
    """Average of a numeric column by a category (statistics template)."""
    column = pick_numeric(tile, model)
    dim = tile.get("dimension")
    if column is None or not isinstance(dim, str) or ":" in dim:
        return TileData(tile_id=tile["id"], type=tile["type"])
    sql = (
        f"SELECT {quote_identifier(dim)} AS category, AVG({quote_identifier(column)}) AS value "
        f"FROM data WHERE {quote_identifier(dim)} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"
    )
    frame = query_frame(con, sql, settings)
    fake = Metric(
        id=column, label=column, expr=f"AVG({column})", unit="count", good_direction="neutral"
    )
    return from_breakdown(frame, tile, fake, locale, "UZS")
    """The numeric column a statistics tile should plot."""
    names = numeric_columns(model)
    return names[0] if names else None


def run_tile(
    con: duckdb.DuckDBPyConnection,
    model: DatasetModel,
    tile: dict[str, Any],
    metrics: dict[str, Metric],
    spec: FilterSpec,
    settings: Settings,
    locale: str,
    currency: str,
) -> TileData:
    """Apply filters, run the tile, then restore the unfiltered view."""
    apply_filters(con, spec, model)
    try:
        return _dispatch(con, model, tile, metrics, settings, locale, currency)
    except (ValidationError, UserFacingError, KeyError, IndexError, ValueError):
        return TileData(tile_id=tile["id"], type=tile["type"])
    finally:
        restore_data(con)


def _dispatch(
    con: duckdb.DuckDBPyConnection,
    model: DatasetModel,
    tile: dict[str, Any],
    metrics: dict[str, Metric],
    settings: Settings,
    locale: str,
    currency: str,
) -> TileData:
    kind = tile["type"]
    metric = metric_of(metrics, tile)
    dim = tile.get("dimension")
    if kind in {"kpi_row", "big_number", "gauge"} and metric:
        return query_kpi(con, tile, metric, model.roles, locale, currency)
    if kind == "line" and metric:
        return query_trend(con, model, tile, metric, settings, locale, currency)
    if kind == "line":
        return query_mean_over_time(con, model, tile, settings, locale)
    if kind == "stacked_bar" and metric:
        return query_stacked(con, model, tile, metric, settings, locale, currency)
    if kind == "heatmap" and dim == "weekday_by_week" and metric:
        return query_heatmap_week(con, model, tile, metric, settings, locale, currency)
    if kind == "heatmap" and dim == "correlation":
        return query_correlation(con, model, tile, locale)
    if kind == "scatter":
        return query_scatter(con, model, tile, locale)
    if kind == "histogram":
        column = (
            dim
            if isinstance(dim, str) and dim not in {"numeric:auto", None}
            else pick_numeric(tile, model)
        )
        return query_histogram(
            con, tile, column or pick_numeric(tile, model) or model.columns[0].name, locale
        )
    if kind == "boxplot":
        column = pick_numeric(tile, model) or model.columns[0].name
        return query_boxplot(con, tile, column, locale)
    if kind == "table" and dim == "describe":
        return query_describe(con, model, tile, locale)
    if kind == "funnel" and metric:
        return query_funnel(con, tile, metric, model, settings, locale, currency)
    if kind == "bullet" and metric:
        return query_bullet(con, tile, metric, model, settings, locale, currency)
    if kind in {"bar_v", "bar_h"} and not metric:
        return query_group_means(con, model, tile, settings, locale)
    if (
        kind in {"donut", "bar_h", "bar_v", "table"}
        and metric
        and isinstance(dim, str)
        and ":" not in dim
        and dim
        not in {
            "correlation",
            "top_pair",
            "describe",
            "stalled_rows",
            "weekday_by_week",
            "numeric:auto",
            "auto",
        }
    ):
        frame = (
            top_table(con, model, metric, dim, settings)
            if kind == "table"
            else breakdown_frame(con, model, metric, dim, 15, settings)
        )
        return from_breakdown(frame, tile, metric, locale, currency)
    if metric:
        return query_kpi(con, tile, metric, model.roles, locale, currency)
    return TileData(tile_id=tile["id"], type=kind)
