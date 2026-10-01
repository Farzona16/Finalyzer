"""Every available metric equals an independent pandas computation (relative error 1e-6)."""

from __future__ import annotations

import math
from collections.abc import Callable

import pandas as pd
import pytest

from core.errors import ValidationError
from core.metrics.builder import metric_sql
from core.metrics.registry import (
    available_metrics,
    choose_pack,
    list_pack_names,
    primary_metric,
    unavailable_metrics,
)
from tests.helpers import DemoDataset, bank_flat, retail_flat, sales_flat, statistics_flat

Roles = dict[str, str]
Reference = Callable[[pd.DataFrame, Roles], float]


def revenue(df: pd.DataFrame, r: Roles) -> float:
    """Paid rows only; without a 'paid' status value the metric falls back to all rows."""
    status = df[r["status"]]
    if (status == "paid").any():
        return df.loc[status == "paid", r["amount"]].sum()
    return df[r["amount"]].sum()


def completed(df: pd.DataFrame, r: Roles) -> pd.DataFrame:
    return df[df[r["status"]] == "completed"]


def cycle_days(df: pd.DataFrame, r: Roles) -> float:
    """Average days between creation and closing of won deals (whole days, as DuckDB counts)."""
    won = df[df[r["status"]] == "won"].dropna(subset=["close_date"])
    created = pd.to_datetime(won[r["date"]])
    closed = pd.to_datetime(won["close_date"])
    return float((closed - created).dt.days.mean())


def stalled(df: pd.DataFrame, r: Roles) -> float:
    """Open deals created more than 90 days before the latest creation date."""
    created = pd.to_datetime(df[r["date"]])
    age = (created.max() - created).dt.days
    return float(((df[r["status"]] == "open") & (age > 90)).sum())


REFERENCES: dict[str, dict[str, Reference]] = {
    "generic": {
        "row_count": lambda df, r: float(len(df)),
        "total_amount": lambda df, r: df[r["amount"]].sum(),
        "avg_amount": lambda df, r: df[r["amount"]].mean(),
        "distinct_customers": lambda df, r: float(df[r["customer_id"]].nunique()),
    },
    "retail": {
        "revenue": revenue,
        "orders": lambda df, r: float(df[r["order_id"]].nunique()),
        "avg_order_value": lambda df, r: df[r["amount"]].sum() / df[r["order_id"]].nunique(),
        "units": lambda df, r: float(df[r["quantity"]].sum()),
        "gross_profit": lambda df, r: df[r["amount"]].sum() - df[r["cost"]].sum(),
        "gross_margin_pct": lambda df, r: 100.0
        * (df[r["amount"]].sum() - df[r["cost"]].sum())
        / df[r["amount"]].sum(),
        "active_customers": lambda df, r: float(df[r["customer_id"]].nunique()),
    },
    "bank": {
        "txn_count": lambda df, r: float(len(df)),
        "txn_volume": lambda df, r: completed(df, r)[r["amount"]].sum(),
        "avg_ticket": lambda df, r: completed(df, r)[r["amount"]].mean(),
        "failed_rate_pct": lambda df, r: 100.0 * (df[r["status"]] == "failed").sum() / len(df),
        "active_customers": lambda df, r: float(df[r["customer_id"]].nunique()),
        "fee_income": lambda df, r: df["fee"].sum(),
    },
    "sales": {
        "pipeline_value": lambda df, r: df.loc[df[r["status"]] == "open", r["amount"]].sum(),
        "won_revenue": lambda df, r: df.loc[df[r["status"]] == "won", r["amount"]].sum(),
        "deals_won": lambda df, r: float((df[r["status"]] == "won").sum()),
        "deals_lost": lambda df, r: float((df[r["status"]] == "lost").sum()),
        "win_rate_pct": lambda df, r: 100.0
        * (df[r["status"]] == "won").sum()
        / df[r["status"]].isin(["won", "lost"]).sum(),
        "avg_deal_size": lambda df, r: df.loc[df[r["status"]] == "won", r["amount"]].mean(),
        "avg_cycle_days": lambda df, r: cycle_days(df, r),
        "new_deals": lambda df, r: float(len(df)),
        "stalled_deals": lambda df, r: stalled(df, r),
    },
    "statistics": {
        "row_count": lambda df, r: float(len(df)),
        "pass_rate_pct": lambda df, r: 100.0 * (df["passed"] == "yes").mean(),
    },
    "project": {},
    "expenses": {
        "total_spend": lambda df, r: df[r["amount"]].sum(),
        "avg_expense": lambda df, r: df[r["amount"]].mean(),
        "expense_count": lambda df, r: float(len(df)),
        "largest_expense": lambda df, r: df[r["amount"]].max(),
    },
}


def scalar(dataset: DemoDataset, sql_expr: str) -> float:
    """Evaluate one aggregate expression on the ``data`` view."""
    return float(dataset.con.execute(f"SELECT {sql_expr} FROM data").fetchone()[0])


def assert_close(actual: float, expected: float) -> None:
    assert math.isclose(actual, float(expected), rel_tol=1e-6, abs_tol=1e-9), (actual, expected)


FLATS = {
    "retail": retail_flat, "bank": bank_flat, "sales": sales_flat, "statistics": statistics_flat,
}
MIN_CHECKED = {"retail": 10, "bank": 10, "sales": 15, "statistics": 4}


@pytest.mark.parametrize("name", ["retail", "bank", "sales", "statistics"])
def test_metrics_match_pandas_for_every_pack(name: str, request: pytest.FixtureRequest) -> None:
    dataset: DemoDataset = request.getfixturevalue(name)
    frame = FLATS[name]()
    roles = dataset.model.roles
    checked = 0
    for pack in list_pack_names():
        for metric in available_metrics(dataset.model, pack):
            actual = scalar(dataset, metric_sql(metric, roles))
            assert_close(actual, REFERENCES[pack][metric.id](frame, roles))
            checked += 1
    assert checked >= MIN_CHECKED[name]


@pytest.mark.parametrize("name", ["retail", "bank"])
def test_metrics_match_pandas_per_group(name: str, request: pytest.FixtureRequest) -> None:
    dataset: DemoDataset = request.getfixturevalue(name)
    frame = retail_flat() if name == "retail" else bank_flat()
    roles = dataset.model.roles
    pack = choose_pack(dataset.model)
    dimension = "category" if name == "retail" else "channel"
    for metric in available_metrics(dataset.model, pack):
        sql = metric_sql(metric, roles)
        rows = dataset.con.execute(f'SELECT "{dimension}", {sql} FROM data GROUP BY 1').fetchall()
        reference = REFERENCES[pack][metric.id]
        for group, value in rows:
            assert_close(float(value), reference(frame[frame[dimension] == group], roles))


def test_pack_choice(retail: DemoDataset, bank: DemoDataset) -> None:
    assert choose_pack(retail.model) == "retail"
    assert choose_pack(bank.model) == "bank"
    no_status = retail.model.model_copy(update={"roles": {"amount": "amount"}})
    assert choose_pack(no_status) == "generic"


def test_primary_metrics(retail: DemoDataset, bank: DemoDataset) -> None:
    assert primary_metric(retail.model).id == "revenue"
    assert primary_metric(bank.model).id == "txn_volume"


def test_metrics_that_need_missing_roles_are_unavailable_not_errors(bank: DemoDataset) -> None:
    unavailable = {s.metric.id: s.reason for s in unavailable_metrics(bank.model, "retail")}
    assert unavailable["units"] == "Missing role: quantity"
    assert unavailable["gross_profit"] == "Missing role: cost"
    available = {m.id: m for m in available_metrics(bank.model, "retail")}
    assert {"orders", "avg_order_value", "active_customers"} <= set(available)
    assert available["revenue"].expr == "SUM({amount})"  # fallback: there is no 'paid' status


def test_retail_revenue_falls_back_to_all_sales_without_paid_status(retail: DemoDataset) -> None:
    columns = [
        c.model_copy(update={"categories": ["done", "open"]}) if c.name == "payment_status" else c
        for c in retail.model.columns
    ]
    model = retail.model.model_copy(update={"columns": columns})
    revenue_metric = next(m for m in available_metrics(model, "retail") if m.id == "revenue")
    assert revenue_metric.expr == "SUM({amount})"


def test_fee_metric_needs_the_fee_column(retail: DemoDataset, bank: DemoDataset) -> None:
    assert "fee_income" in {m.id for m in available_metrics(bank.model, "bank")}
    reasons = {s.metric.id: s.reason for s in unavailable_metrics(retail.model, "bank")}
    assert reasons["fee_income"] == "Missing column: fee"


def test_builder_refuses_unknown_and_unmapped_placeholders(retail: DemoDataset) -> None:
    base = available_metrics(retail.model, "generic")[0]
    with pytest.raises(ValidationError, match="Unknown placeholder"):
        metric_sql(base.model_copy(update={"expr": "SUM({foo})"}), retail.model.roles)
    with pytest.raises(ValidationError, match="not mapped"):
        metric_sql(base.model_copy(update={"expr": "SUM({cost})"}), {"amount": "amount"})
    with pytest.raises(ValidationError, match="Curly braces"):
        metric_sql(base.model_copy(update={"expr": "SUM({amount}) + '{'"}), {"amount": "amount"})


def test_metric_overrides_replace_expressions(retail: DemoDataset) -> None:
    metrics = available_metrics(retail.model, "generic", {"row_count": "COUNT(*) + 1"})
    assert next(m for m in metrics if m.id == "row_count").expr == "COUNT(*) + 1"
