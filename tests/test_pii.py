"""PII detection by name hints, value patterns and the Luhn check."""

from __future__ import annotations

import pandas as pd
import pytest

from core.ingest.pii import find_pii_columns, looks_like_card, looks_like_phone, luhn_valid
from tests.helpers import DemoDataset

VALID_CARD = "4539 1488 0343 6467"


def test_luhn() -> None:
    assert luhn_valid("4539148803436467")
    assert not luhn_valid("4539148803436468")


def test_card_and_phone_patterns() -> None:
    assert looks_like_card(VALID_CARD)
    assert not looks_like_card("4539 1488 0343 6468")
    assert looks_like_phone("+1 (555) 010-9999")
    assert not looks_like_phone("1234567")  # plain digits are usually amounts or ids
    assert not looks_like_phone("12 Main Street")


def test_name_hints() -> None:
    frame = pd.DataFrame(
        {
            "customer_name": ["a"], "product_name": ["p"], "store_name": ["s"], "email": ["x"],
            "phone_number": ["1"], "card_number": ["1"], "amount": ["1"], "city": ["c"],
        }
    )
    found = find_pii_columns(frame)
    assert set(found) == {"customer_name", "email", "phone_number", "card_number"}


def test_value_patterns_are_detected_with_neutral_column_names() -> None:
    frame = pd.DataFrame(
        {
            "contact": [f"user{i}@example.com" for i in range(10)],
            "reference": [VALID_CARD] * 10,
            "line": ["+44 20 7946 0958"] * 10,
            "note": ["hello"] * 10,
        }
    )
    assert set(find_pii_columns(frame)) == {"contact", "reference", "line"}


def test_id_columns_are_not_flagged_by_value_rules() -> None:
    frame = pd.DataFrame({"account_id": ["+44 20 7946 0958"] * 10, "id": [VALID_CARD] * 10})
    assert find_pii_columns(frame) == {}


def test_minority_matches_are_ignored() -> None:
    values = ["a@b.co"] * 2 + ["plain text"] * 8
    assert find_pii_columns(pd.DataFrame({"remarks": values})) == {}


@pytest.mark.parametrize(
    ("dataset_name", "table", "expected"),
    [
        ("retail", "customers", {"name", "phone", "email"}),
        ("bank", "customers", {"full_name", "phone"}),
    ],
)
def test_demo_pii_columns_found_and_removed_from_base(
    dataset_name: str, table: str, expected: set[str], retail: DemoDataset, bank: DemoDataset
) -> None:
    dataset = {"retail": retail, "bank": bank}[dataset_name]
    assert set(dataset.workspace.pii[table]) == expected
    flat_columns = {c.name for c in dataset.model.columns}
    assert not expected & flat_columns
    assert {f"{table}.{c}" for c in expected} <= set(dataset.model.dropped_pii)
    # no other table has PII in the demo data
    others = {t: cols for t, cols in dataset.workspace.pii.items() if t != table}
    assert all(not cols for cols in others.values())
