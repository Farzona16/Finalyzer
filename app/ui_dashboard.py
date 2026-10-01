"""Dashboard tab and sidebar slicers (Streamlit). Figures are built from fixed templates."""

from __future__ import annotations

from datetime import date

import duckdb
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.i18n import t
from app.theme import CSS, PRIMARY, SERIES, kpi_card_html, register_template
from core.config import Settings
from core.dashboard.chartspecs import (
    breakdown_frame,
    can_overlay_previous_year,
    data_span,
    default_dimension,
    dimension_options,
    last_period_is_partial,
    preview_frame,
    trend_frame,
)
from core.dashboard.filters import choose_slicers, describe_filters, filter_hash, find_column
from core.dashboard.kpi import KpiValue, compute_kpis, format_value
from core.models import DatasetModel, FilterSpec, Metric

KPI_COLUMNS = 3
Connection = duckdb.DuckDBPyConnection


# ---------------------------------------------------------------- sidebar slicers


def widget_key(model: DatasetModel, name: str) -> str:
    """Streamlit widget key that is unique per dataset."""
    return f"{model.dataset_id}:{name}"


def full_date_range(model: DatasetModel) -> tuple[date, date] | None:
    """The whole span of the date column, from the dataset profile."""
    column = model.roles.get("date")
    if not column:
        return None
    info = find_column(model, column)
    if not info.min_value or not info.max_value:
        return None
    return date.fromisoformat(str(info.min_value)), date.fromisoformat(str(info.max_value))


def date_slicer(model: DatasetModel) -> tuple[date, date] | None:
    """Date range input; returns a range only when it is narrower than the whole span."""
    full = full_date_range(model)
    if full is None:
        return None
    picked = st.date_input(
        t("filters.date"),
        value=full,
        min_value=full[0],
        max_value=full[1],
        key=widget_key(model, "date"),
    )
    if isinstance(picked, tuple) and len(picked) == 2 and tuple(picked) != full:
        return picked[0], picked[1]
    return None


def category_slicers(model: DatasetModel) -> dict[str, list[str]]:
    """One multi-select per chosen low-cardinality column."""
    chosen: dict[str, list[str]] = {}
    by_name = {c.name: c for c in model.columns}
    for name in choose_slicers(model):
        picked = st.multiselect(
            name.replace("_", " ").capitalize(),
            by_name[name].categories or [],
            key=widget_key(model, f"cat:{name}"),
            placeholder=t("filters.all"),
        )
        if picked:
            chosen[name] = picked
    return chosen


def amount_slicer(model: DatasetModel) -> dict[str, tuple[float, float]]:
    """Numeric range slider for the amount column (active only when moved)."""
    column = model.roles.get("amount")
    if not column:
        return {}
    info = find_column(model, column)
    if info.min_value is None or info.max_value is None or info.min_value >= info.max_value:
        return {}
    low, high = float(info.min_value), float(info.max_value)
    picked = st.slider(
        column.replace("_", " ").capitalize(),
        low,
        high,
        (low, high),
        key=widget_key(model, "amount"),
    )
    return {column: (float(picked[0]), float(picked[1]))} if picked != (low, high) else {}


def clear_filters(model: DatasetModel) -> None:
    """Callback of the Clear filters button: forget every slicer widget of this dataset."""
    prefix = f"{model.dataset_id}:"
    for key in [k for k in st.session_state if str(k).startswith(prefix)]:
        del st.session_state[key]


def render_sidebar_filters(model: DatasetModel) -> FilterSpec:
    """Draw the slicers in the current container and return the resulting ``FilterSpec``."""
    st.subheader(t("filters.title"))
    date_range = date_slicer(model)
    categorical = category_slicers(model)
    numeric = amount_slicer(model)
    st.button(t("filters.clear"), on_click=clear_filters, args=(model,))
    return FilterSpec(date_range=date_range, categorical=categorical, numeric=numeric)


def render_filter_summary(model: DatasetModel, spec: FilterSpec, rows: int) -> None:
    """Always-visible line: active filters and how many rows remain."""
    st.caption(
        t(
            "filters.summary",
            filters=describe_filters(spec),
            rows=f"{rows:,}",
            total=f"{model.row_count:,}",
        )
    )


# ---------------------------------------------------------------- cached queries
# Underscore-prefixed arguments are not hashed by Streamlit. The cache key is made of the
# dataset version, the filter hash and the metric definitions.


@st.cache_data(show_spinner=False, max_entries=64)
def cached_kpis(
    _con: Connection,
    _model: DatasetModel,
    _metrics: list[Metric],
    _settings: Settings,
    _spec: FilterSpec,
    version: str,
    fhash: str,
    mkey: str,
) -> list[KpiValue]:
    """KPI cards for the current filters."""
    return compute_kpis(_con, _model, _metrics, _spec, _settings)


@st.cache_data(show_spinner=False, max_entries=64)
def cached_trend(
    _con: Connection,
    _model: DatasetModel,
    _metric: Metric,
    _settings: Settings,
    version: str,
    fhash: str,
    mkey: str,
    overlay: bool,
) -> pd.DataFrame:
    """Trend data of one metric."""
    return trend_frame(_con, _model, _metric, overlay, _settings)


@st.cache_data(show_spinner=False, max_entries=64)
def cached_breakdown(
    _con: Connection,
    _model: DatasetModel,
    _metric: Metric,
    _settings: Settings,
    version: str,
    fhash: str,
    mkey: str,
    dimension: str,
) -> pd.DataFrame:
    """Top categories of one dimension."""
    return breakdown_frame(_con, _model, _metric, dimension, settings=_settings)


@st.cache_data(show_spinner=False, max_entries=16)
def cached_default_dimension(
    _con: Connection,
    _model: DatasetModel,
    _metric: Metric,
    _settings: Settings,
    version: str,
    fhash: str,
    mkey: str,
) -> str | None:
    """The default breakdown dimension."""
    return default_dimension(_con, _model, _metric, _settings)


def metric_key(metrics: list[Metric]) -> str:
    """Cache key part that changes when a metric definition is edited."""
    return "|".join(f"{m.id}={m.expr}" for m in metrics)


# ---------------------------------------------------------------- figures


def unit_suffix(metric: Metric) -> str:
    """Axis label: metric name plus a unit hint."""
    return f"{metric.label} (%)" if metric.unit == "percent" else metric.label


def trend_figure(frame: pd.DataFrame, metric: Metric, overlay: bool) -> go.Figure:
    """Line chart of a metric over time, with last year's line when requested."""
    fig = go.Figure(layout={"template": register_template()})
    fig.add_scatter(x=frame["period"], y=frame["value"], name=metric.label, mode="lines+markers")
    if overlay and "previous_year_value" in frame:
        fig.add_scatter(
            x=frame["period"],
            y=frame["previous_year_value"],
            name=t("dash.last_year"),
            mode="lines",
            line={"dash": "dot", "color": SERIES[1]},
        )
    fig.update_layout(
        title=t("dash.trend_title", metric=metric.label), yaxis_title=unit_suffix(metric)
    )
    return fig


def breakdown_figure(frame: pd.DataFrame, metric: Metric, dimension: str) -> go.Figure:
    """Horizontal bar chart of the top categories."""
    ordered = frame.sort_values("value")
    fig = go.Figure(layout={"template": register_template()})
    fig.add_bar(
        x=ordered["value"], y=ordered["category"].astype(str), orientation="h",
        marker_color=PRIMARY,
    )
    title = t("dash.breakdown_title", metric=metric.label, dimension=dimension.replace("_", " "))
    fig.update_layout(title=title, xaxis_title=unit_suffix(metric))
    return fig


def top_table_display(frame: pd.DataFrame, metric: Metric, dimension: str) -> pd.DataFrame:
    """Top-N table with readable numbers."""
    table = pd.DataFrame(
        {
            dimension.replace("_", " ").capitalize(): frame["category"].astype(str),
            metric.label: [format_value(v, metric.unit) for v in frame["value"]],
        }
    )
    if frame["share_pct"].notna().any():
        table[t("dash.share")] = [f"{v:.1f}%" for v in frame["share_pct"]]
    return table


# ---------------------------------------------------------------- sections


def render_kpis(cards: list[KpiValue]) -> None:
    """KPI cards in rows of three."""
    for start in range(0, len(cards), KPI_COLUMNS):
        columns = st.columns(KPI_COLUMNS)
        for column, card in zip(columns, cards[start : start + KPI_COLUMNS], strict=False):
            column.markdown(kpi_card_html(card, t("dash.vs_previous")), unsafe_allow_html=True)


def render_trends(
    con: Connection,
    model: DatasetModel,
    metrics: list[Metric],
    settings: Settings,
    keys: tuple[str, str, str],
) -> None:
    """Primary trend (with previous-year toggle) and a second, selectable metric trend."""
    primary = metrics[0]
    overlay = False
    if can_overlay_previous_year(con, model):
        overlay = st.toggle(t("dash.overlay"), key=widget_key(model, "overlay"))
    frame = cached_trend(con, model, primary, settings, *keys, overlay)
    if frame.empty:
        st.info(t("dash.no_trend"))
        return
    st.plotly_chart(trend_figure(frame, primary, overlay))
    span = data_span(con, model)
    if span and last_period_is_partial(frame, span[1]):
        st.caption(t("dash.partial"))
    others = [m for m in metrics if m.id != primary.id]
    if not others:
        return
    chosen = st.selectbox(
        t("dash.second_metric"),
        others,
        format_func=lambda m: m.label,
        key=widget_key(model, "second_metric"),
    )
    second = cached_trend(con, model, chosen, settings, *keys, False)
    if not second.empty:
        st.plotly_chart(trend_figure(second, chosen, False))


def render_breakdown(
    con: Connection,
    model: DatasetModel,
    metrics: list[Metric],
    settings: Settings,
    keys: tuple[str, str, str],
) -> None:
    """Breakdown by a chosen dimension and the Top-10 table."""
    options = dimension_options(model)
    if not options:
        st.info(t("dash.no_dimensions"))
        return
    primary = metrics[0]
    default = cached_default_dimension(con, model, primary, settings, *keys)
    dimension = st.selectbox(
        t("dash.dimension"),
        options,
        index=options.index(default) if default in options else 0,
        format_func=lambda name: name.replace("_", " ").capitalize(),
        key=widget_key(model, "dimension"),
    )
    frame = cached_breakdown(con, model, primary, settings, *keys, dimension)
    if frame.empty:
        st.info(t("dash.empty"))
        return
    left, right = st.columns([3, 2])
    left.plotly_chart(breakdown_figure(frame, primary, dimension))
    right.markdown(f"**{t('dash.top10')}**")
    right.dataframe(top_table_display(frame.head(10), primary, dimension), hide_index=True)


def render_dashboard(
    con: Connection,
    model: DatasetModel,
    metrics: list[Metric],
    spec: FilterSpec,
    settings: Settings,
    rows: int,
) -> None:
    """The Dashboard tab: KPIs, trend, breakdown, Top-10 and a data preview."""
    st.markdown(CSS, unsafe_allow_html=True)
    if rows == 0:
        st.info(t("dash.empty"))
        return
    if not metrics:
        st.warning(t("dash.no_metrics"))
        return
    # The dataset id keeps cached results of different sessions apart even for equal versions.
    keys = (f"{model.dataset_id}-{model.version}", filter_hash(spec), metric_key(metrics))
    render_kpis(cached_kpis(con, model, metrics, settings, spec, *keys))
    render_trends(con, model, metrics, settings, keys)
    render_breakdown(con, model, metrics, settings, keys)
    with st.expander(t("dash.preview")):
        st.dataframe(preview_frame(con))
