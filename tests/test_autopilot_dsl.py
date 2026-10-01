"""The restricted metric language: valid objects compile, everything else is refused safely."""

from __future__ import annotations

import math
import random
from typing import Any

import pytest
import sqlglot
from sqlglot import exp

from core.autopilot.dsl import (
    AGGS,
    OPS,
    Context,
    DslError,
    Spec,
    canonical,
    compile_sql,
    parse_spec,
    to_dict,
)
from core.models import ColumnInfo, DatasetModel
from core.query.sql_validator import validate_and_explain
from tests.helpers import DemoDataset

FUZZ_OBJECTS = 400  # twice the 200 the acceptance test asks for


@pytest.fixture(scope="module")
def ctx(sales: DemoDataset) -> Context:
    return Context.from_model(sales.model)


def won_rate() -> dict[str, Any]:
    return {
        "agg": "ratio",
        "times100": True,
        "unit": "percent",
        "numerator": {"agg": "count", "filter": [{"column": "status", "op": "=", "value": "won"}]},
        "denominator": {"agg": "count"},
    }


def test_documented_example_compiles(ctx: Context) -> None:
    spec = parse_spec(
        {
            "id": "avg_cycle_days",
            "agg": "avg_days_between",
            "columns": ["created_date", "close_date"],
            "filter": [{"column": "status", "op": "=", "value": "won"}],
            "unit": "count",
            "good_direction": "down",
            "label": {"en": "Average sales cycle", "uz": "Oʻrtacha sotuv davri"},
        },
        ctx,
    )
    assert compile_sql(spec, ctx) == (
        'AVG(date_diff(\'day\', CAST("created_date" AS DATE), CAST("close_date" AS DATE))) '
        "FILTER (WHERE \"status\" = 'won')"
    )
    assert spec.labels["uz"] == "Oʻrtacha sotuv davri"


def test_ratio_compiles_with_a_guarded_denominator(ctx: Context) -> None:
    sql = compile_sql(parse_spec(won_rate(), ctx), ctx)
    assert sql.startswith("100.0 * (COUNT(*) FILTER (WHERE \"status\" = 'won')) / NULLIF((COUNT(*)")


@pytest.mark.parametrize(
    "agg", [a for a in AGGS if a not in ("ratio", "avg_days_between", "count")]
)
def test_each_aggregation_runs(agg: str, ctx: Context, sales: DemoDataset) -> None:
    raw: dict[str, Any] = {"agg": agg, "columns": ["amount"]}
    if agg == "count_distinct":
        raw["columns"] = ["region"]
    if agg == "quantile":
        raw["q"] = 0.9
    sql = compile_sql(parse_spec(raw, ctx), ctx)
    assert sales.con.execute(f"SELECT {sql} FROM data").fetchone()[0] is not None


@pytest.mark.parametrize(
    "raw",
    [
        {"agg": "sum", "columns": ["nope"]},
        {"agg": "sum", "columns": ["status"]},  # text
        {"agg": "sum", "columns": ["deal_id"]},  # identifier
        {"agg": "sum", "columns": ["amount", "amount"]},
        {"agg": "sum", "columns": []},
        {"agg": "drop", "columns": ["amount"]},
        {"agg": "quantile", "columns": ["amount"], "q": 1.5},
        {"agg": "sum", "columns": ["amount"], "q": 0.5},
        {"agg": "avg_days_between", "columns": ["created_date", "amount"]},
        {"agg": "avg_days_between", "columns": ["created_date"]},
        {"agg": "count_distinct", "columns": []},
        {
            "agg": "sum",
            "columns": ["amount"],
            "filter": [{"column": "status", "op": "=", "value": "bogus"}],
        },
        {
            "agg": "sum",
            "columns": ["amount"],
            "filter": [{"column": "status", "op": "LIKE", "value": "w%"}],
        },
        {
            "agg": "sum",
            "columns": ["amount"],
            "filter": [{"column": "status", "op": ">", "value": "won"}],
        },
        {
            "agg": "sum",
            "columns": ["amount"],
            "filter": [{"column": "amount", "op": ">", "value": "5"}],
        },
        {
            "agg": "sum",
            "columns": ["amount"],
            "filter": [{"column": "status", "op": "in", "value": "won"}],
        },
        {"agg": "sum", "columns": ["amount"], "filter": [{"column": "x", "op": "is_null"}]},
        {"agg": "sum", "columns": ["amount"], "filter": "status = 'won'"},
        {"agg": "sum", "columns": ["amount"], "id": "Bad Id; DROP"},
        {"agg": "sum", "columns": ["amount"], "unit": "dollars"},
        {"agg": "ratio", "numerator": {"agg": "count"}},
        {"agg": "ratio", "numerator": won_rate(), "denominator": {"agg": "count"}},
        {
            "agg": "sum",
            "columns": ["amount"],
            "filter": [{"column": "amount", "op": ">", "value": 1e30}],
        },
        {
            "agg": "sum",
            "columns": ["amount"],
            "filter": [{"column": "amount", "op": ">", "value": math.nan}],
        },
        "SELECT 1",
        None,
        [],
    ],
)
def test_invalid_metrics_are_refused(raw: Any, ctx: Context) -> None:
    with pytest.raises(DslError):
        parse_spec(raw, ctx)


def test_category_values_match_case_insensitively_to_the_known_spelling(ctx: Context) -> None:
    spec = parse_spec(
        {"agg": "count", "filter": [{"column": "status", "op": "in", "value": ["WON", "Lost"]}]},
        ctx,
    )
    assert spec.filters[0].value == ("won", "lost")


def test_personal_data_columns_are_refused() -> None:
    columns = [
        ColumnInfo(name="email", dtype="text", is_pii=True, distinct=90, categories=["a"]),
        ColumnInfo(name="size", dtype="float", distinct=50),
    ]
    model = DatasetModel(
        dataset_id="x",
        fact_table="t",
        tables={"t": 100},
        columns=columns,
        row_count=100,
        version="1",
    )
    context = Context.from_model(model)
    with pytest.raises(DslError):
        parse_spec({"agg": "count_distinct", "columns": ["email"]}, context)
    assert parse_spec({"agg": "avg", "columns": ["size"]}, context).agg == "avg"


def test_quotes_in_a_category_are_escaped() -> None:
    columns = [
        ColumnInfo(name="who", dtype="text", distinct=3, categories=["O'Brien", "Smith", "Lee"])
    ]
    model = DatasetModel(
        dataset_id="x", fact_table="t", tables={"t": 10}, columns=columns, row_count=10, version="1"
    )
    context = Context.from_model(model)
    spec = parse_spec(
        {"agg": "count", "filter": [{"column": "who", "op": "=", "value": "O'Brien"}]}, context
    )
    sql = compile_sql(spec, context)
    assert "'O''Brien'" in sql
    tree = sqlglot.parse_one(f"SELECT {sql} FROM data", dialect="duckdb")
    assert tree.find(exp.Literal).name == "O'Brien"


def test_round_trip_keeps_the_meaning(ctx: Context) -> None:
    spec = parse_spec(won_rate(), ctx)
    again = parse_spec(to_dict(spec), ctx)
    assert canonical(again) == canonical(spec)
    assert compile_sql(again, ctx) == compile_sql(spec, ctx)


def test_canonical_ignores_labels_ids_and_filter_order(ctx: Context) -> None:
    clauses = [
        {"column": "status", "op": "=", "value": "won"},
        {"column": "source", "op": "=", "value": "website"},
    ]
    first = parse_spec({"agg": "sum", "columns": ["amount"], "filter": clauses, "id": "a"}, ctx)
    second = parse_spec(
        {"agg": "sum", "columns": ["amount"], "filter": clauses[::-1], "label": "other"}, ctx
    )
    assert canonical(first) == canonical(second)


# ----------------------------------------------------------------------------- fuzz

HOSTILE = [
    None,
    True,
    False,
    0,
    -1,
    3,
    2.5,
    10**30,
    float("inf"),
    float("nan"),
    "",
    "x",
    "won",
    "'; DROP TABLE data; --",
    "') OR 1=1 --",
    "{amount}",
    "\x00",
    "a" * 500,
    "O'Brien",
    "Robert'); DROP TABLE students;--",
    "\u202e",
    "UNION SELECT 1",
    [],
    {},
    [1, 2],
    {"a": 1},
    ["won"],
    [None],
    ["amount", "status"],
    {"column": "amount"},
    "read_csv('/etc/passwd')",
]


def garbage(r: random.Random, depth: int = 0) -> Any:
    if depth < 3 and r.random() < 0.25:
        return {r.choice(["agg", "columns", "filter", "op", "value", "q"]): garbage(r, depth + 1)}
    return r.choice(HOSTILE)


def plausible(r: random.Random, columns: list[str], depth: int = 0) -> dict[str, Any]:
    agg = r.choice([*AGGS, "evil", None, 7])
    raw: dict[str, Any] = {"agg": agg, "unit": r.choice(["count", "percent", "usd", None])}
    if agg == "ratio" and depth < 2:
        raw["numerator"] = plausible(r, columns, depth + 1)
        raw["denominator"] = plausible(r, columns, depth + 1)
        raw["times100"] = r.choice([True, False, "yes"])
        return raw
    raw["columns"] = r.choice(
        [
            [r.choice(columns)],
            [r.choice(columns), r.choice(columns)],
            r.choice(columns),
            garbage(r),
            [],
        ]
    )
    if r.random() < 0.7:
        raw["filter"] = [
            {
                "column": r.choice([*columns, garbage(r)]),
                "op": r.choice([*OPS, "LIKE", garbage(r)]),
                "value": r.choice([garbage(r), "won", "lost", 5, "2025-01-01", ["won", "open"]]),
            }
            for _ in range(r.randint(0, 7))
        ]
    if r.random() < 0.3:
        raw["q"] = r.choice([0.5, 0.0, 1.0, garbage(r)])
    return raw


def mostly_valid(r: random.Random, ctx: Context) -> dict[str, Any]:
    """A valid object for this dataset, corrupted in one field about one time in five."""
    numeric = [
        n for n, i in ctx.columns.items() if i.dtype in ("integer", "float") and n != "deal_id"
    ]
    text = [n for n, i in ctx.columns.items() if i.categories]
    agg = r.choice(["sum", "avg", "min", "max", "median", "stddev", "count", "count_distinct"])
    column = r.choice(text if agg == "count_distinct" else numeric)
    raw: dict[str, Any] = {"agg": agg, "columns": [column]}
    if r.random() < 0.6:
        filter_column = r.choice(text)
        values = ctx.columns[filter_column].categories or []
        op = r.choice(["=", "!=", "in"])
        value = r.sample(values, min(2, len(values))) if op == "in" else r.choice(values)
        raw["filter"] = [{"column": filter_column, "op": op, "value": value}]
    if r.random() < 0.2:
        raw[r.choice(["agg", "columns", "filter", "unit"])] = garbage(r)
    return raw


def make_objects(ctx: Context) -> list[Any]:
    columns = list(ctx.columns)
    r = random.Random(20260930)
    objects: list[Any] = [mostly_valid(r, ctx) for _ in range(FUZZ_OBJECTS // 4)]
    objects += [plausible(r, columns) for _ in range(FUZZ_OBJECTS // 2)]
    objects += [garbage(r) for _ in range(FUZZ_OBJECTS // 4)]
    objects += [
        {k: garbage(r) for k in ("agg", "columns", "filter", "q")} for _ in range(FUZZ_OBJECTS // 4)
    ]
    return objects


def test_fuzz_never_yields_unsafe_sql_or_an_unexpected_exception(
    ctx: Context, sales: DemoDataset
) -> None:
    columns = list(ctx.columns)
    accepted = 0
    for raw in make_objects(ctx):
        try:
            spec = parse_spec(raw, ctx)
        except DslError:
            continue
        accepted += 1
        sql = compile_sql(spec, ctx)
        select = f"SELECT {sql} AS value FROM data"
        validate_and_explain(sales.con, select)  # the normal SQL guard accepts it
        tree = sqlglot.parse_one(select, dialect="duckdb")
        used = {c.name for c in tree.find_all(exp.Column)}
        assert used <= set(columns)
        assert {t.name for t in tree.find_all(exp.Table)} == {"data"}
        assert not tree.find(exp.Subquery, exp.Command, exp.Insert, exp.Drop)
        sales.con.execute(select).fetchall()  # and it runs
    assert accepted >= 60  # the generator does reach valid objects, so the check above is real


def test_fuzz_corpus_has_the_documented_size(ctx: Context) -> None:
    assert len(make_objects(ctx)) >= 200


def test_spec_is_a_plain_value(ctx: Context) -> None:
    assert isinstance(parse_spec({"agg": "count"}, ctx), Spec)
