"""Ask answers include the actual numbers, reasoning and the compiled SQL."""

from __future__ import annotations

from core.models import Metric, QueryResult
from core.nlu.ask import from_nl
from core.nlu.plain_answer import compose
from core.nlu.router import Route
from core.query.nl2sql import Answer


def _route() -> Route:
    metric = Metric(
        id="revenue",
        label="Revenue",
        expr="SUM({amount})",
        unit="currency",
        good_direction="up",
    )
    return Route(intent="total", language="uz", metric=metric, confident=True)


def test_a_kpi_answer_states_the_value_and_the_sql() -> None:
    result = QueryResult(
        sql="SELECT SUM(amount) FROM data",
        columns=["revenue"],
        rows=[[12_500_000]],
        row_count=1,
    )
    answer = compose(_route(), result, "uz", "simple", "UZS", [])
    assert "12" in answer.headline
    assert "12" in answer.explanation
    assert "qator" not in answer.headline
    assert answer.sql == result.sql
    assert "total" in answer.details
    assert "SUM" in answer.details


def test_a_table_answer_lists_values_not_only_a_row_count() -> None:
    result = QueryResult(
        sql="SELECT category, SUM(amount) FROM data GROUP BY 1",
        columns=["category", "revenue"],
        rows=[["A", 100], ["B", 50]],
        row_count=2,
    )
    answer = compose(_route(), result, "en", "expert", "USD", [])
    assert "A" in answer.headline and "100" in answer.explanation
    assert "2 rows" in answer.explanation
    assert "compiled by code" in answer.details
    assert answer.sql == result.sql
    assert result.sql not in answer.details


def test_an_llm_wrap_keeps_sql_and_reasoning() -> None:
    result = QueryResult(sql="SELECT 1", columns=["n"], rows=[[1]], row_count=1)
    wrapped = from_nl(Answer(status="ok", text="N: 1.", result=result), "en", "simple")
    assert wrapped.explanation == "N: 1."
    assert wrapped.sql == "SELECT 1"
    assert "generated" in wrapped.details.lower()
