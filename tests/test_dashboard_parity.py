"""Every KPI on screen equals a pandas recomputation on the filtered data (random filters)."""

from __future__ import annotations

import math
import random
from datetime import date, timedelta

import pandas as pd
import pytest

from core.dashboard.filters import apply_filters, choose_slicers
from core.dashboard.kpi import (
    MAX_KPIS,
    compute_delta,
    compute_kpis,
    delta_tone,
    format_delta,
    format_value,
)
from core.metrics.registry import available_metrics, choose_pack
from core.metrics.timeintel import previous_range
from core.models import FilterSpec
from tests.helpers import DemoDataset, bank_flat, retail_flat
from tests.test_metrics_parity import REFERENCES

COMBINATIONS = 10


@pytest.fixture(autouse=True)
def reset_filters(retail, bank):
    yield
    apply_filters(retail.con, FilterSpec(), retail.model)
    apply_filters(bank.con, FilterSpec(), bank.model)


def random_spec(dataset: DemoDataset, frame: pd.DataFrame, rng: random.Random) -> FilterSpec:
    """A random mix of date range, up to two category filters and an amount range."""
    roles = dataset.model.roles
    days = sorted(frame[roles["date"]].unique())
    start = rng.choice(days[: len(days) // 2])
    end = start + timedelta(days=rng.randint(60, 200))
    categorical: dict[str, list[str]] = {}
    for name in rng.sample(choose_slicers(dataset.model), k=rng.randint(0, 2)):
        values = sorted(frame[name].dropna().unique())
        categorical[name] = rng.sample(values, k=max(1, len(values) // 2))
    amount = frame[roles["amount"]].dropna()
    low, high = sorted(amount.quantile([rng.uniform(0, 0.2), rng.uniform(0.6, 1.0)]))
    numeric = {roles["amount"]: (float(low), float(high))} if rng.random() < 0.5 else {}
    return FilterSpec(date_range=(start, end), categorical=categorical, numeric=numeric)


def pandas_filter(
    frame: pd.DataFrame, spec: FilterSpec, roles: dict[str, str], span: tuple[date, date]
) -> pd.DataFrame:
    """The same filters applied with pandas (independent of the SQL code)."""
    mask = (frame[roles["date"]] >= span[0]) & (frame[roles["date"]] <= span[1])
    for name, values in spec.categorical.items():
        mask &= frame[name].isin(values)
    for name, (low, high) in spec.numeric.items():
        mask &= frame[name].between(low, high)
    return frame[mask]


def close(actual: float | None, expected: float) -> bool:
    if actual is None:
        return expected is None or (isinstance(expected, float) and math.isnan(expected))
    return math.isclose(actual, float(expected), rel_tol=1e-6, abs_tol=1e-9)


@pytest.mark.parametrize("name", ["retail", "bank"])
def test_kpis_match_pandas_for_random_filters(name: str, request) -> None:
    dataset: DemoDataset = request.getfixturevalue(name)
    frame = retail_flat() if name == "retail" else bank_flat()
    roles = dataset.model.roles
    pack = choose_pack(dataset.model)
    metrics = available_metrics(dataset.model, pack)
    rng = random.Random(20260930)
    checked = 0
    for _ in range(COMBINATIONS):
        spec = random_spec(dataset, frame, rng)
        current = pandas_filter(frame, spec, roles, spec.date_range)
        if apply_filters(dataset.con, spec, dataset.model) == 0:
            continue
        assert len(current) == dataset.con.execute("SELECT COUNT(*) FROM data").fetchone()[0]
        before = previous_range(*spec.date_range)
        previous = pandas_filter(frame, spec, roles, before)
        cards = compute_kpis(dataset.con, dataset.model, metrics, spec, limit=len(metrics))
        for card, metric in zip(cards, metrics, strict=True):
            reference = REFERENCES[pack][metric.id]
            assert close(card.value, reference(current, roles)), (metric.id, spec)
            if previous.empty:
                assert card.previous is None and card.delta_display == "n/a"
            else:
                assert close(card.previous, reference(previous, roles)), (metric.id, spec)
            checked += 1
    assert checked >= COMBINATIONS * 4


def test_compute_kpis_filters_current_period_without_mutating_data(retail) -> None:
    """A date-range spec restricts current values even when the `data` view is unfiltered."""
    apply_filters(retail.con, FilterSpec(), retail.model)
    full = retail.con.execute("SELECT COUNT(*) FROM data").fetchone()[0]
    metrics = available_metrics(retail.model)
    all_time = compute_kpis(retail.con, retail.model, metrics, FilterSpec(), limit=1)[0]
    spec = FilterSpec(date_range=(date(2026, 9, 1), date(2026, 9, 30)))
    month = compute_kpis(retail.con, retail.model, metrics, spec, limit=1)[0]
    assert retail.con.execute("SELECT COUNT(*) FROM data").fetchone()[0] == full
    assert month.value is not None and all_time.value is not None
    assert month.value != all_time.value


def test_default_span_compares_with_nothing_before_the_data(retail) -> None:
    metrics = available_metrics(retail.model)
    cards = compute_kpis(retail.con, retail.model, metrics, FilterSpec())
    assert 1 <= len(cards) <= MAX_KPIS
    assert cards[0].metric_id == "revenue"
    assert all(c.previous is None and c.delta_display == "n/a" for c in cards)


def test_empty_filter_result_does_not_raise(retail) -> None:
    spec = FilterSpec(categorical={"channel": ["nothing-like-this"]})
    assert apply_filters(retail.con, spec, retail.model) == 0
    cards = compute_kpis(retail.con, retail.model, available_metrics(retail.model), spec)
    assert all(c.value is None or c.value == 0 for c in cards)


def test_delta_rules() -> None:
    assert compute_delta(110, 100, "currency") == (pytest.approx(10.0), "pct")
    assert compute_delta(35, 30, "percent") == (5, "pp")
    assert compute_delta(5, 0, "currency") == (None, None)
    assert compute_delta(None, 3, "count") == (None, None)
    assert delta_tone(10, "up") == "good" and delta_tone(10, "down") == "bad"
    assert delta_tone(-3, "down") == "good" and delta_tone(10, "neutral") == "neutral"
    assert delta_tone(0.01, "up") == "neutral" and delta_tone(None, "up") == "neutral"
    assert format_delta(12.34, "pct") == "+12.3%" and format_delta(-1.5, "pp") == "-1.5 pts"
    assert format_delta(None, None) == "n/a"


def test_value_formatting() -> None:
    assert format_value(None, "count") == "n/a"
    assert format_value(1234.4, "count") == "1,234"
    assert format_value(2_500_000, "currency") == "2.50M"
    assert format_value(12.345, "percent") == "12.3%"
    assert format_value(0.456, "ratio") == "0.46"
