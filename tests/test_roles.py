"""Role detection from column names and types."""

from __future__ import annotations

from core.ingest.roles import detect_roles
from core.models import ColumnInfo
from tests.helpers import DemoDataset

RETAIL_ROLES = {
    "date": "sale_date",
    "amount": "amount",
    "cost": "cost_amount",
    "quantity": "quantity",
    "customer_id": "customer_id",
    "order_id": "sale_id",
    "status": "payment_status",
}
BANK_ROLES = {
    "date": "txn_date",
    "amount": "amount",
    "customer_id": "customer_id",
    "order_id": "txn_id",
    "status": "status",
}


def col(name: str, dtype: str = "text") -> ColumnInfo:
    """Build a minimal column description."""
    return ColumnInfo(name=name, dtype=dtype)


def test_demo_roles_match_appendix_a(retail: DemoDataset, bank: DemoDataset) -> None:
    assert retail.model.roles == RETAIL_ROLES
    assert bank.model.roles == BANK_ROLES


def test_role_is_stored_on_the_column(retail: DemoDataset) -> None:
    by_name = {c.name: c for c in retail.model.columns}
    assert by_name["sale_date"].role == "date"
    assert by_name["city"].role is None


def test_type_must_fit_the_role() -> None:
    columns = [col("amount", "text"), col("order_date", "text"), col("qty", "text")]
    assert detect_roles(columns) == {}


def test_synonym_priority_and_contains_match() -> None:
    columns = [col("revenue", "float"), col("amount", "float"), col("sales_total_usd", "float")]
    assert detect_roles(columns)["amount"] == "amount"  # exact synonym with best priority
    columns = [col("net_revenue_usd", "float")]
    assert detect_roles(columns)["amount"] == "net_revenue_usd"  # contains-match


def test_a_column_serves_only_one_role() -> None:
    columns = [col("id", "integer"), col("customer", "text")]
    roles = detect_roles(columns)
    assert roles == {"customer_id": "customer", "order_id": "id"}


def test_pii_columns_are_never_used() -> None:
    pii = ColumnInfo(name="customer_id", dtype="text", is_pii=True)
    assert "customer_id" not in detect_roles([pii])
