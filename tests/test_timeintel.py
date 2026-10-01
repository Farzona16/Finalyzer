"""Time-intelligence SQL builders run on both demo datasets and give correct numbers."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from core.errors import ValidationError
from core.metrics.registry import available_metrics, primary_metric
from core.metrics.timeintel import (
    choose_grain,
    period_compare,
    previous_range,
    series,
    share_of_total,
    top_n,
    yoy,
    ytd,
)
from tests.helpers import DemoDataset, retail_flat


def fetch(dataset: DemoDataset, sql: str) -> list[tuple]:
    return dataset.con.execute(sql).fetchall()


@pytest.mark.parametrize("name", ["retail", "bank"])
def test_every_builder_executes(name: str, request: pytest.FixtureRequest) -> None:
    dataset: DemoDataset = request.getfixturevalue(name)
    roles = dataset.model.roles
    metric = primary_metric(dataset.model)
    dimension = "category" if name == "retail" else "branch_name"
    for grain in ("day", "week", "month", "quarter"):
        assert fetch(dataset, series(metric, roles, grain))
    assert fetch(dataset, yoy(metric, roles, "month"))
    assert fetch(dataset, yoy(metric, roles, "week"))
    assert fetch(dataset, ytd(metric, roles))
    assert fetch(dataset, share_of_total(metric, roles, dimension))
    assert len(fetch(dataset, top_n(metric, roles, dimension, 3))) == 3
    sql = period_compare(metric, roles, date(2026, 9, 1), date(2026, 9, 30))
    current, previous = fetch(dataset, sql)[0]
    assert current is not None and previous is not None


def test_grain_choice() -> None:
    assert choose_grain(date(2026, 1, 1), date(2026, 3, 3)) == "day"  # 62 days
    assert choose_grain(date(2026, 1, 1), date(2026, 3, 4)) == "week"  # 63 days
    assert choose_grain(date(2026, 1, 1), date(2026, 6, 29)) == "week"  # 180 days
    assert choose_grain(date(2026, 1, 1), date(2026, 6, 30)) == "month"  # 181 days


def test_previous_range_has_equal_length() -> None:
    assert previous_range(date(2026, 9, 1), date(2026, 9, 30)) == (
        date(2026, 8, 2),
        date(2026, 8, 31),
    )


def test_monthly_series_and_yoy_and_period_compare_match_pandas(retail: DemoDataset) -> None:
    frame = retail_flat()
    frame = frame[frame["payment_status"] == "paid"].copy()
    frame["month"] = pd.to_datetime(frame["sale_date"]).dt.to_period("M").dt.to_timestamp()
    expected = frame.groupby("month")["amount"].sum()
    metric, roles = primary_metric(retail.model), retail.model.roles

    rows = fetch(retail, series(metric, roles, "month"))
    assert len(rows) == 24
    for period, value in rows:
        assert value == pytest.approx(expected[pd.Timestamp(period)], rel=1e-9)

    yoy_sql = yoy(metric, roles, "month")
    yoy_rows = {pd.Timestamp(p): (v, pv, pct) for p, v, pv, pct in fetch(retail, yoy_sql)}
    september = yoy_rows[pd.Timestamp("2025-10-01")]
    assert september[1] == pytest.approx(expected[pd.Timestamp("2024-10-01")], rel=1e-9)
    assert september[2] == pytest.approx(
        100 * (september[0] - september[1]) / september[1], rel=1e-9
    )
    assert yoy_rows[pd.Timestamp("2024-10-01")][1] is None

    compare_sql = period_compare(metric, roles, date(2026, 9, 1), date(2026, 9, 30))
    current, previous = fetch(retail, compare_sql)[0]
    assert current == pytest.approx(expected[pd.Timestamp("2026-09-01")], rel=1e-9)
    in_range = (frame["sale_date"] >= date(2026, 8, 2)) & (frame["sale_date"] <= date(2026, 8, 31))
    assert previous == pytest.approx(frame[in_range]["amount"].sum(), rel=1e-9)


def test_ytd_and_share(retail: DemoDataset) -> None:
    metric, roles = primary_metric(retail.model), retail.model.roles
    ytd_value, previous_ytd = fetch(retail, ytd(metric, roles))[0]
    frame = retail_flat()
    frame = frame[frame["payment_status"] == "paid"]
    in_2026 = frame[frame["sale_date"] >= date(2026, 1, 1)]
    days = frame["sale_date"]
    in_2025 = frame[(days >= date(2025, 1, 1)) & (days <= date(2025, 9, 30))]
    assert ytd_value == pytest.approx(in_2026["amount"].sum(), rel=1e-9)
    assert previous_ytd == pytest.approx(in_2025["amount"].sum(), rel=1e-9)
    shares = fetch(retail, share_of_total(metric, roles, "category"))
    assert sum(row[2] for row in shares) == pytest.approx(100.0)
    assert shares[0][0] == "Electronics"


def test_missing_date_role_is_a_validation_error(retail: DemoDataset) -> None:
    metric = available_metrics(retail.model, "generic")[0]
    with pytest.raises(ValidationError, match="date"):
        series(metric, {"amount": "amount"}, "month")
    with pytest.raises(ValidationError, match="grain"):
        series(metric, retail.model.roles, "decade")
