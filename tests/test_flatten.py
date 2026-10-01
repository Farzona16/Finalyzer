"""Flat view creation, column naming and connection lock-down."""

from __future__ import annotations

import duckdb
import pandas as pd
import pytest

from core.errors import ValidationError
from core.ingest.flatten import build_dataset, open_workspace
from core.models import Relationship
from tests.helpers import DemoDataset

RETAIL_FLAT = {
    "sale_id", "sale_date", "store_id", "product_id", "customer_id", "channel", "quantity",
    "amount", "discount", "cost_amount", "payment_status", "product_name", "category",
    "unit_cost", "store_name", "region", "city", "signup_date", "segment",
}
BANK_FLAT = {
    "txn_id", "txn_date", "customer_id", "branch_id", "channel", "txn_type", "amount", "status",
    "fee", "branch_name", "region", "city", "segment", "join_date",
}


def test_flat_columns_match_appendix_a(retail: DemoDataset, bank: DemoDataset) -> None:
    assert {c.name for c in retail.model.columns} == RETAIL_FLAT
    assert {c.name for c in bank.model.columns} == BANK_FLAT


def test_row_counts_and_views(retail: DemoDataset) -> None:
    con = retail.con
    assert con.execute("SELECT COUNT(*) FROM base").fetchone()[0] == retail.model.row_count
    assert con.execute("SELECT COUNT(*) FROM data").fetchone()[0] == retail.model.row_count
    assert retail.model.row_count == retail.model.tables["sales"]


def test_dates_are_real_dates(retail: DemoDataset) -> None:
    dtype = retail.con.execute("SELECT typeof(sale_date) FROM data LIMIT 1").fetchone()[0]
    assert dtype == "DATE"
    by_name = {c.name: c for c in retail.model.columns}
    assert by_name["sale_date"].dtype == "date"
    assert by_name["sale_date"].min_value == "2024-10-01"
    assert by_name["sale_date"].max_value == "2026-09-30"


def test_profile_of_categories_and_nulls(retail: DemoDataset) -> None:
    by_name = {c.name: c for c in retail.model.columns}
    assert set(by_name["channel"].categories or []) == {"online", "in-store"}
    assert 28 <= by_name["discount"].null_pct <= 32
    assert by_name["sale_id"].categories is None


def test_version_is_stable_and_changes_with_roles(retail: DemoDataset) -> None:
    workspace = retail.workspace
    rels = retail.model.relationships
    again = build_dataset(workspace, "sales", rels)
    assert again.version == retail.model.version
    changed = build_dataset(workspace, "sales", rels, roles={"amount": "discount"})
    assert changed.version != retail.model.version
    build_dataset(workspace, "sales", rels)  # restore the shared fixture state


def test_unknown_role_column_is_rejected(retail: DemoDataset) -> None:
    with pytest.raises(ValidationError):
        build_dataset(retail.workspace, "sales", retail.model.relationships, roles={"amount": "x"})


@pytest.mark.parametrize(
    "roles, message",
    [
        ({"amount": "channel"}, "cannot be used as amount"),
        ({"date": "amount"}, "cannot be used as date"),
        ({"profit": "amount"}, "Unknown roles"),
    ],
)
def test_role_mapping_is_checked_against_column_types(
    retail: DemoDataset, roles: dict[str, str], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        build_dataset(retail.workspace, "sales", retail.model.relationships, roles=roles)
    build_dataset(retail.workspace, "sales", retail.model.relationships)  # restore fixture


def test_collision_gets_table_prefix() -> None:
    facts = pd.DataFrame({"k": [1, 2, 2], "other": ["a", "b", "c"], "label": ["f1", "f2", "f3"]})
    dims = pd.DataFrame({"k": [1, 2], "label": ["d1", "d2"]})
    workspace = open_workspace({"facts": facts, "dims": dims})
    rel = Relationship(fact_col="k", dim_table="dims", dim_col="k", containment=1.0)
    model = build_dataset(workspace, "facts", [rel])
    assert [c.name for c in model.columns] == ["k", "other", "label", "dims_label"]
    rows = workspace.con.execute("SELECT dims_label FROM base ORDER BY other").fetchall()
    assert rows == [("d1",), ("d2",), ("d2",)]


def test_non_unique_join_key_is_refused() -> None:
    facts = pd.DataFrame({"k": [1, 2]})
    dims = pd.DataFrame({"k": [1, 1, 2], "v": ["a", "b", "c"]})
    workspace = open_workspace({"facts": facts, "dims": dims})
    rel = Relationship(fact_col="k", dim_table="dims", dim_col="k", containment=1.0)
    with pytest.raises(ValidationError, match="not unique"):
        build_dataset(workspace, "facts", [rel])


def test_connection_is_locked_down(retail: DemoDataset) -> None:
    con = retail.con
    with pytest.raises(duckdb.Error):
        con.execute("SET enable_external_access=true")
    with pytest.raises(duckdb.Error):
        con.execute("SELECT * FROM read_csv('data/demo/retail/stores.csv')")
    with pytest.raises(duckdb.Error):
        con.execute("COPY (SELECT 1) TO 'leak.csv'")


def test_workspace_without_date_column_still_picks_a_fact_table() -> None:
    from core.ingest.flatten import pick_fact_table

    workspace = open_workspace(
        {"small": pd.DataFrame({"a": [1]}), "large": pd.DataFrame({"a": [1, 2, 3]})}
    )
    assert pick_fact_table(workspace) == "large"
