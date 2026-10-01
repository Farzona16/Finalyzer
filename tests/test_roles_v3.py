"""The v3 roles (stage, owner, source, target) and the Uzbek/Russian fallback."""

from __future__ import annotations

import pytest

from core.ingest.roles import detect_roles
from core.models import ColumnInfo


def col(name: str, dtype: str = "text") -> ColumnInfo:
    """Build a minimal column description."""
    return ColumnInfo(name=name, dtype=dtype)  # type: ignore[arg-type]


def test_sales_pipeline_roles_are_found() -> None:
    columns = [
        col("deal_id"),
        col("created_date", "date"),
        col("owner_id"),
        col("source"),
        col("stage"),
        col("status"),
        col("amount", "float"),
    ]
    roles = detect_roles(columns)
    assert roles["stage"] == "stage"
    assert roles["owner"] == "owner_id"
    assert roles["source"] == "source"
    assert roles["date"] == "created_date"
    assert roles["amount"] == "amount"
    assert roles["status"] == "status"


def test_target_role_needs_a_number() -> None:
    assert detect_roles([col("target", "float")]).get("target") == "target"
    assert "target" not in detect_roles([col("target", "text")])


@pytest.mark.parametrize(
    ("name", "dtype", "role"),
    [
        ("sana", "date", "date"),
        ("summa", "float", "amount"),
        ("mijoz_id", "text", "customer_id"),
        ("holat", "text", "status"),
        ("manba", "text", "source"),
        ("bosqich", "text", "stage"),
        ("дата", "date", "date"),
        ("сумма", "float", "amount"),
    ],
)
def test_uzbek_and_russian_names_fall_back(name: str, dtype: str, role: str) -> None:
    assert detect_roles([col(name, dtype)]).get(role) == name


def test_english_names_win_over_the_fallback() -> None:
    columns = [col("sana", "date"), col("order_date", "date")]
    assert detect_roles(columns)["date"] == "order_date"


def test_pii_columns_are_never_roles() -> None:
    pii = ColumnInfo(name="owner", dtype="text", is_pii=True)
    assert "owner" not in detect_roles([pii])


def test_a_single_date_column_is_the_date_even_with_an_unknown_name() -> None:
    roles = detect_roles([col("sample_id"), col("measured_at", "date"), col("pressure", "float")])
    assert roles["date"] == "measured_at"


def test_two_unknown_date_columns_are_not_guessed() -> None:
    roles = detect_roles([col("opened", "date"), col("closed", "date")])
    assert "date" not in roles


def test_the_single_date_rule_does_not_override_a_named_date() -> None:
    roles = detect_roles([col("order_date", "date"), col("x", "float")])
    assert roles["date"] == "order_date"

