"""Helpers shared by several test modules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd

from core.ingest.flatten import (
    Workspace,
    build_dataset,
    pick_fact_table,
    propose,
    workspace_from_raw,
)
from core.ingest.loader import load_folder
from core.models import DatasetModel

DEMO_DIR = Path("data/demo")
FIXTURES = Path("tests/fixtures")


@dataclass
class DemoDataset:
    """A demo dataset ready for tests: workspace (with connection) and dataset model."""

    name: str
    workspace: Workspace
    model: DatasetModel

    @property
    def con(self) -> duckdb.DuckDBPyConnection:
        """The DuckDB connection holding ``base`` and ``data``."""
        return self.workspace.con


def read_demo_csv(name: str, date_columns: list[str]) -> pd.DataFrame:
    """Read one demo CSV with plain pandas, converting the given columns to dates."""
    frame = pd.read_csv(DEMO_DIR / name)
    for column in date_columns:
        frame[column] = pd.to_datetime(frame[column]).dt.date
    return frame


def retail_flat() -> pd.DataFrame:
    """Retail sales joined to products, stores and customers with pandas (independent of core)."""
    sales = read_demo_csv("retail/sales.csv", ["sale_date"])
    products = read_demo_csv("retail/products.csv", [])
    stores = read_demo_csv("retail/stores.csv", [])
    customers = read_demo_csv("retail/customers.csv", ["signup_date"]).drop(
        columns=["name", "email", "phone"]
    )
    return (
        sales.merge(products, on="product_id", how="left")
        .merge(stores, on="store_id", how="left")
        .merge(customers, on="customer_id", how="left")
    )


def bank_flat() -> pd.DataFrame:
    """Bank transactions joined to branches and customers with pandas."""
    txns = read_demo_csv("bank/transactions.csv", ["txn_date"])
    branches = read_demo_csv("bank/branches.csv", [])
    customers = read_demo_csv("bank/customers.csv", ["join_date"]).drop(
        columns=["full_name", "phone"]
    )
    return txns.merge(branches, on="branch_id", how="left").merge(
        customers, on="customer_id", how="left"
    )


def build_demo(name: str) -> DemoDataset:
    """Load, clean, join and describe one demo dataset."""
    workspace = workspace_from_raw(load_folder(DEMO_DIR / name))
    fact = pick_fact_table(workspace)
    model = build_dataset(workspace, fact, propose(workspace, fact))
    return DemoDataset(name, workspace, model)
