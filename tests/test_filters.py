"""Slicers: escaped literals, validated columns, and the rebuilt ``data`` view."""

from __future__ import annotations

from datetime import date

import pytest

from core.dashboard.filters import (
    MAX_SLICERS,
    apply_filters,
    choose_slicers,
    describe_filters,
    filter_hash,
    is_active,
    sql_number,
    sql_string,
    where_sql,
)
from core.errors import ValidationError
from core.models import FilterSpec
from tests.helpers import retail_flat


@pytest.fixture(autouse=True)
def reset_filters(retail, bank):
    """The demo datasets are shared, so every test leaves ``data`` unfiltered."""
    yield
    apply_filters(retail.con, FilterSpec(), retail.model)
    apply_filters(bank.con, FilterSpec(), bank.model)


def count(dataset) -> int:
    return dataset.con.execute("SELECT COUNT(*) FROM data").fetchone()[0]


def test_no_filters_gives_no_where(retail) -> None:
    assert where_sql(FilterSpec(), retail.model) == ""
    assert not is_active(FilterSpec())
    assert describe_filters(FilterSpec()) == "none"


def test_literals_are_escaped() -> None:
    assert sql_string("O'Brien") == "'O''Brien'"
    assert sql_string("x' OR '1'='1") == "'x'' OR ''1''=''1'"
    assert sql_number(5) == "5.0"
    with pytest.raises(ValidationError):
        sql_string("a\x00b")
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            sql_number(bad)


@pytest.mark.parametrize(
    "value", ["x' OR '1'='1", "'; DROP VIEW base; --", "\\' OR 1=1 --", "%", "online' --"]
)
def test_injection_values_match_nothing_and_change_nothing(retail, value: str) -> None:
    total = retail.model.row_count
    spec = FilterSpec(categorical={"channel": [value]})
    assert apply_filters(retail.con, spec, retail.model) == 0
    assert retail.con.execute("SELECT COUNT(*) FROM base").fetchone()[0] == total
    assert apply_filters(retail.con, FilterSpec(), retail.model) == total


def test_unknown_or_hostile_column_names_are_rejected(retail) -> None:
    for name in ('channel" OR 1=1 --', "no_such_column", "customers"):
        with pytest.raises(ValidationError):
            where_sql(FilterSpec(categorical={name: ["x"]}), retail.model)
        with pytest.raises(ValidationError):
            where_sql(FilterSpec(numeric={name: (0, 1)}), retail.model)


def test_numeric_filter_needs_a_numeric_column(retail) -> None:
    with pytest.raises(ValidationError):
        where_sql(FilterSpec(numeric={"channel": (0, 1)}), retail.model)


def test_date_filter_needs_a_date_role(retail) -> None:
    model = retail.model.model_copy(update={"roles": {"amount": "amount"}})
    spec = FilterSpec(date_range=(date(2026, 1, 1), date(2026, 1, 31)))
    with pytest.raises(ValidationError):
        where_sql(spec, model)


def test_filters_match_pandas(retail) -> None:
    frame = retail_flat()
    spec = FilterSpec(
        date_range=(date(2026, 2, 1), date(2026, 5, 31)),
        categorical={"channel": ["online"], "category": ["Electronics", "Toys"]},
        numeric={"amount": (20.0, 900.0)},
    )
    expected = frame[
        (frame["sale_date"] >= date(2026, 2, 1))
        & (frame["sale_date"] <= date(2026, 5, 31))
        & frame["channel"].isin(["online"])
        & frame["category"].isin(["Electronics", "Toys"])
        & frame["amount"].between(20.0, 900.0)
    ]
    assert apply_filters(retail.con, spec, retail.model) == len(expected) > 0
    assert count(retail) == len(expected)


def test_empty_selection_and_reversed_range_are_handled(retail) -> None:
    total = retail.model.row_count
    assert apply_filters(retail.con, FilterSpec(categorical={"channel": []}), retail.model) == total
    reversed_range = FilterSpec(numeric={"amount": (900.0, 20.0)})
    normal_range = FilterSpec(numeric={"amount": (20.0, 900.0)})
    assert apply_filters(retail.con, reversed_range, retail.model) == apply_filters(
        retail.con, normal_range, retail.model
    )


def test_date_override_replaces_only_the_date(retail) -> None:
    spec = FilterSpec(
        date_range=(date(2026, 1, 1), date(2026, 1, 31)), categorical={"channel": ["online"]}
    )
    override = (date(2025, 12, 1), date(2025, 12, 31))
    text = where_sql(spec, retail.model, date_override=override)
    assert "2025-12-01" in text and "2026-01-01" not in text and "'online'" in text


def test_describe_and_hash(retail) -> None:
    spec = FilterSpec(
        date_range=(date(2026, 1, 1), date(2026, 1, 31)),
        categorical={"channel": ["online", "store"]},
        numeric={"amount": (1, 50)},
    )
    assert is_active(spec)
    text = describe_filters(spec)
    assert "dates 2026-01-01 to 2026-01-31" in text and "channel: online, store" in text
    assert "online" not in describe_filters(spec, include_values=False)
    assert filter_hash(spec) == filter_hash(spec.model_copy(deep=True))
    assert filter_hash(spec) != filter_hash(FilterSpec())


@pytest.mark.parametrize("name", ["retail", "bank"])
def test_slicer_choice(name, request) -> None:
    dataset = request.getfixturevalue(name)
    slicers = choose_slicers(dataset.model)
    assert 2 <= len(slicers) <= MAX_SLICERS
    by_name = {c.name: c for c in dataset.model.columns}
    for column in slicers:
        info = by_name[column]
        assert info.dtype == "text" and not info.is_pii and 2 <= info.distinct <= 30
    assert not {"sale_id", "customer_id", "txn_id"} & set(slicers)
