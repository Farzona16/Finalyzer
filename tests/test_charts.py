"""Deterministic chart choice and result descriptions."""

from __future__ import annotations

from core.models import QueryResult
from core.query.charts import choose_chart, column_kind
from core.query.nl2sql import describe_result, format_number


def result(columns: list[str], rows: list[list]) -> QueryResult:
    return QueryResult(sql="SELECT 1", columns=columns, rows=rows, row_count=len(rows))


def test_single_value_is_a_kpi() -> None:
    assert choose_chart(result(["n"], [[5]])).kind == "kpi"


def test_empty_result_is_a_table() -> None:
    assert choose_chart(result(["n"], [])).kind == "table"


def test_date_and_numbers_make_a_line() -> None:
    spec = choose_chart(result(["month", "revenue"], [["2026-01-01", 1.0], ["2026-02-01", 2.0]]))
    assert (spec.kind, spec.x, spec.y) == ("line", "month", ["revenue"])


def test_categories_make_a_bar_or_horizontal_bar() -> None:
    few = choose_chart(result(["store", "rev"], [[f"S{i}", i] for i in range(5)]))
    many = choose_chart(result(["store", "rev"], [[f"S{i}", i] for i in range(12)]))
    assert few.kind == "bar" and many.kind == "hbar" and many.limit == 15


def test_two_measures_make_grouped_bars() -> None:
    rows = [["a", 1, 2.5], ["b", 2, 3.5]]
    assert choose_chart(result(["k", "x", "y"], rows)).kind == "grouped_bar"


def test_other_shapes_and_table_hint_give_a_table() -> None:
    assert choose_chart(result(["a", "b", "c"], [["x", "y", 1]])).kind == "table"
    rows = [["a", 1], ["b", 2]]
    assert choose_chart(result(["k", "v"], rows), hint="table").kind == "table"
    assert choose_chart(result(["k", "v"], rows), hint="line").kind == "bar"


def test_column_kind() -> None:
    assert column_kind(["2026-01-01", None]) == "date"
    assert column_kind(["2026-01-01 10:00:00"]) == "date"
    assert column_kind(["a", "2026-01-01"]) == "text"
    assert column_kind([1, 2.5, None]) == "number"
    assert column_kind([True, False]) == "other"
    assert column_kind([None]) == "other"


def test_number_formatting_and_descriptions() -> None:
    assert format_number(1234567) == "1,234,567"
    assert format_number(1234.5) == "1,234.50"
    assert format_number(3.0) == "3"
    assert describe_result(result(["total_revenue"], [[1500.0]])) == "Total revenue: 1,500."
    assert "no rows" in describe_result(result(["a"], []))
    text = describe_result(result(["store", "rev"], [["A", 1], ["B", 5]]))
    assert "Highest: B" in text
    assert "3 rows and 2 columns" in describe_result(
        result(["a", "b"], [["x", "y"], ["x", "z"], ["q", "r"]])
    )
    mixed = describe_result(result(["a", "b"], [["x", "y"], ["x", "z"], ["q", "r"]]))
    assert "Values:" in mixed and "x, y" in mixed
