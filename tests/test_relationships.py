"""Relationship proposals between fact and dimension tables."""

from __future__ import annotations

import pandas as pd

from core.ingest.flatten import build_dataset, open_workspace, propose
from tests.helpers import DemoDataset

RETAIL_EXPECTED = {
    ("customer_id", "customers", "customer_id"),
    ("product_id", "products", "product_id"),
    ("store_id", "stores", "store_id"),
}
BANK_EXPECTED = {
    ("customer_id", "customers", "customer_id"),
    ("branch_id", "branches", "branch_id"),
}


def as_set(relationships) -> set[tuple[str, str, str]]:  # noqa: ANN001
    """Reduce relationships to comparable tuples."""
    return {(r.fact_col, r.dim_table, r.dim_col) for r in relationships}


def test_demo_relationships_are_proposed(retail: DemoDataset, bank: DemoDataset) -> None:
    assert as_set(retail.model.relationships) == RETAIL_EXPECTED
    assert as_set(bank.model.relationships) == BANK_EXPECTED
    for dataset in (retail, bank):
        assert all(r.containment >= 0.95 for r in dataset.model.relationships)
        assert all(r.cardinality == "many_to_one" for r in dataset.model.relationships)


def test_fact_table_is_picked_automatically(retail: DemoDataset, bank: DemoDataset) -> None:
    assert retail.model.fact_table == "sales"
    assert bank.model.fact_table == "transactions"


def test_name_similarity_beats_coincidental_overlap() -> None:
    # "qty" values 1..3 are all valid store ids, but "store" is the real link.
    facts = pd.DataFrame({"store": [1, 2, 3, 1, 2, 3] * 5, "qty": [1, 2, 3, 3, 2, 1] * 5})
    facts["day"] = pd.to_datetime("2024-01-01")
    stores = pd.DataFrame({"store_id": [1, 2, 3], "label": ["a", "b", "c"]})
    workspace = open_workspace({"facts": facts, "stores": stores})
    found = propose(workspace, "facts")
    assert as_set(found) == {("store", "stores", "store_id")}


def test_integer_and_text_keys_with_similar_names_are_joined() -> None:
    facts = pd.DataFrame({"store_id": [1, 2, 3, 1, 2, 3], "amount": [10, 20, 30, 11, 21, 31]})
    stores = pd.DataFrame({"store_id": ["1", "2", "3"], "region": ["North", "South", "East"]})
    workspace = open_workspace({"facts": facts, "stores": stores})
    found = propose(workspace, "facts")
    assert as_set(found) == {("store_id", "stores", "store_id")}
    model = build_dataset(workspace, "facts", found)
    assert "region" in {c.name for c in model.columns}
    rows = {r[0] for r in workspace.con.execute("SELECT DISTINCT region FROM data").fetchall()}
    assert rows == {"North", "South", "East"}


def test_low_containment_is_not_proposed() -> None:
    facts = pd.DataFrame({"ref": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]})
    dims = pd.DataFrame({"ref_id": [1, 2, 3], "label": ["a", "b", "c"]})
    workspace = open_workspace({"facts": facts, "dims": dims})
    assert propose(workspace, "facts") == []


def test_non_unique_dimension_column_is_not_a_key() -> None:
    facts = pd.DataFrame({"code": ["a", "b", "a", "b"]})
    dims = pd.DataFrame({"code": ["a", "a", "b"], "label": ["x", "y", "z"]})
    workspace = open_workspace({"facts": facts, "dims": dims})
    assert propose(workspace, "facts") == []
