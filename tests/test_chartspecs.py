"""Chart data: trend, previous-year overlay, breakdown, top-N, defaults and preview."""

from __future__ import annotations

import math
from datetime import date

import pytest

from core.dashboard.chartspecs import (
    PREVIEW_ROWS,
    breakdown_frame,
    can_overlay_previous_year,
    data_span,
    default_dimension,
    dimension_options,
    last_period_is_partial,
    preview_frame,
    top_table,
    trend_frame,
)
from core.dashboard.filters import apply_filters
from core.errors import ValidationError
from core.metrics.registry import available_metrics, primary_metric
from core.models import FilterSpec
from tests.helpers import retail_flat


@pytest.fixture(autouse=True)
def reset_filters(retail, bank):
    yield
    apply_filters(retail.con, FilterSpec(), retail.model)
    apply_filters(bank.con, FilterSpec(), bank.model)


def test_trend_adds_up_to_the_total(retail) -> None:
    metric = primary_metric(retail.model)
    frame = trend_frame(retail.con, retail.model, metric)
    total = retail.con.execute(
        "SELECT SUM(amount) FILTER (WHERE payment_status = 'paid') FROM data"
    ).fetchone()[0]
    assert frame.attrs["grain"] == "month"
    assert list(frame.columns) == ["period", "value"]
    assert math.isclose(frame["value"].sum(), total, rel_tol=1e-9)
    assert frame["period"].is_monotonic_increasing


def test_trend_grain_follows_the_selected_range(retail) -> None:
    metric = primary_metric(retail.model)
    apply_filters(
        retail.con,
        FilterSpec(date_range=(date(2026, 8, 1), date(2026, 8, 31))),
        retail.model,
    )
    assert trend_frame(retail.con, retail.model, metric).attrs["grain"] == "day"


def test_previous_year_overlay(retail) -> None:
    metric = primary_metric(retail.model)
    assert can_overlay_previous_year(retail.con, retail.model)
    frame = trend_frame(retail.con, retail.model, metric, overlay_previous_year=True)
    assert list(frame.columns) == ["period", "value", "previous_year_value"]
    assert frame["previous_year_value"].notna().sum() >= 12
    apply_filters(
        retail.con, FilterSpec(date_range=(date(2026, 1, 1), date(2026, 6, 30))), retail.model
    )
    assert not can_overlay_previous_year(retail.con, retail.model)


def test_partial_last_period_is_detected(retail) -> None:
    metric = primary_metric(retail.model)
    apply_filters(
        retail.con, FilterSpec(date_range=(date(2025, 1, 1), date(2026, 9, 15))), retail.model
    )
    frame = trend_frame(retail.con, retail.model, metric)
    assert last_period_is_partial(frame, data_span(retail.con, retail.model)[1])
    apply_filters(
        retail.con, FilterSpec(date_range=(date(2025, 1, 1), date(2026, 8, 31))), retail.model
    )
    frame = trend_frame(retail.con, retail.model, metric)
    assert not last_period_is_partial(frame, data_span(retail.con, retail.model)[1])


def test_breakdown_matches_pandas_and_shares_add_up(retail) -> None:
    metric = primary_metric(retail.model)
    frame = breakdown_frame(retail.con, retail.model, metric, "category", limit=50)
    flat = retail_flat()
    expected = flat[flat["payment_status"] == "paid"].groupby("category")["amount"].sum()
    assert set(frame["category"]) == set(expected.index)
    for _, row in frame.iterrows():
        assert math.isclose(row["value"], expected[row["category"]], rel_tol=1e-9)
    assert math.isclose(frame["share_pct"].sum(), 100.0, rel_tol=1e-9)
    assert frame["value"].is_monotonic_decreasing


def test_top_table_has_ten_rows_at_most(retail) -> None:
    metric = primary_metric(retail.model)
    frame = top_table(retail.con, retail.model, metric, "store_name")
    assert 1 <= len(frame) <= 10


def test_share_is_empty_for_non_additive_metrics(retail) -> None:
    average = next(m for m in available_metrics(retail.model) if not m.additive)
    frame = breakdown_frame(retail.con, retail.model, average, "category")
    assert frame["share_pct"].isna().all()


def test_breakdown_rejects_bad_dimensions(retail) -> None:
    metric = primary_metric(retail.model)
    with pytest.raises(ValidationError):
        breakdown_frame(retail.con, retail.model, metric, "amount")
    with pytest.raises(ValidationError):
        breakdown_frame(retail.con, retail.model, metric, 'x"; DROP VIEW base; --')


def test_default_dimension_is_a_valid_option(retail, bank) -> None:
    for dataset in (retail, bank):
        metric = primary_metric(dataset.model)
        chosen = default_dimension(dataset.con, dataset.model, metric)
        assert chosen in dimension_options(dataset.model)


def test_preview_is_limited_and_has_no_personal_columns(retail) -> None:
    frame = preview_frame(retail.con)
    assert len(frame) == PREVIEW_ROWS
    assert not {"name", "email", "phone"} & set(frame.columns)


def test_empty_data_gives_empty_frames_not_errors(retail) -> None:
    metric = primary_metric(retail.model)
    apply_filters(retail.con, FilterSpec(categorical={"channel": ["none"]}), retail.model)
    assert data_span(retail.con, retail.model) is None
    assert trend_frame(retail.con, retail.model, metric).empty
    assert not can_overlay_previous_year(retail.con, retail.model)
    assert breakdown_frame(retail.con, retail.model, metric, "category").empty
    assert preview_frame(retail.con).empty
