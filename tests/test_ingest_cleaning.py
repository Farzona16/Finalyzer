"""Loading and cleaning of messy files."""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

from core.config import Settings
from core.errors import UserFacingError
from core.ingest.cleaner import clean_table, normalize_number_text
from core.ingest.loader import load_file, load_files, snake_case

FIXTURES = Path("tests/fixtures")


def load_fixture(name: str) -> pd.DataFrame:
    """Load a fixture file and return its only table (raw text)."""
    tables = load_file(name, (FIXTURES / name).read_bytes())
    assert len(tables) == 1
    return next(iter(tables.values()))


def test_mixed_date_formats_are_parsed_with_warning() -> None:
    result = clean_table(load_fixture("messy_dates.csv"), "orders")
    dates = result.frame["order_date"]
    assert str(dates.dtype).startswith("datetime64")
    expected = [date(2024, 3, 15), date(2024, 3, 15), date(2024, 3, 16), date(2024, 3, 17)]
    assert [d.date() for d in dates.dropna()] == expected
    assert dates.isna().sum() == 1
    assert any("mixes several date formats" in w for w in result.warnings)
    assert any(a.action == "time_of_day_dropped" for a in result.actions)
    assert result.frame["amount"].dtype == "int64"


def test_ambiguous_dates_use_day_first_and_warn() -> None:
    result = clean_table(load_fixture("messy_ambiguous_dates.csv"), "t")
    assert result.frame["when"].iloc[0].date() == date(2024, 2, 1)
    assert any("ambiguous" in w for w in result.warnings)


def test_formatted_numbers_and_semicolon_delimiter() -> None:
    result = clean_table(load_fixture("messy_numbers.csv"), "t")
    frame = result.frame
    assert list(frame.columns) == ["id", "price", "qty", "note"]
    assert frame["id"].dtype == "int64"
    assert frame["qty"].dtype == "int64"
    assert frame["price"].dtype == "float64"
    assert frame["price"].dropna().tolist() == [1234.5, 1234.5, -12.5, 2500.75]
    assert frame["price"].isna().sum() == 1
    assert frame["note"].dtype == object
    assert frame["note"].notna().sum() == 2  # N/A, null and - became empty


def test_duplicates_are_reported_not_removed_by_default() -> None:
    raw = load_fixture("messy_duplicates.csv")
    kept = clean_table(raw, "t")
    assert len(kept.frame) == 4  # the fully empty row is dropped
    assert any(a.action == "duplicate_rows_found" and a.count == 1 for a in kept.actions)
    assert any(a.action == "dropped_empty_rows" for a in kept.actions)
    assert kept.frame["name"].tolist() == ["Ann", "Ann", "Bob", "Carl"]
    assert kept.frame["city"].isna().sum() == 1
    removed = clean_table(raw, "t", remove_duplicates=True)
    assert len(removed.frame) == 3


def test_original_frame_is_not_modified() -> None:
    raw = load_fixture("messy_duplicates.csv")
    before = raw.copy(deep=True)
    clean_table(raw, "t", remove_duplicates=True)
    pd.testing.assert_frame_equal(raw, before)


def test_cp1251_encoding_is_detected() -> None:
    result = clean_table(load_fixture("messy_cp1251.csv"), "t")
    frame = result.frame
    assert list(frame.columns) == ["город", "сумма", "дата"]
    assert frame["город"].tolist() == ["Москва", "Казань", "Сочи"]
    assert frame["сумма"].tolist() == [1200.5, 300.0, 45.5]
    assert str(frame["дата"].dtype).startswith("datetime64")


def test_identifiers_with_leading_zeros_stay_text() -> None:
    raw = pd.DataFrame({"code": ["007", "012", "100"], "n": ["1", "2", "3"]})
    frame = clean_table(raw, "t").frame
    assert frame["code"].tolist() == ["007", "012", "100"]
    assert frame["n"].dtype == "int64"


def test_duplicate_and_odd_column_names() -> None:
    raw = pd.DataFrame([["1", "2", "3"]], columns=[" Order Date ", "Order-Date", "  "])
    assert list(clean_table(raw, "t").frame.columns) == ["order_date", "order_date_2", "column"]


def test_yes_no_columns_become_boolean() -> None:
    raw = pd.DataFrame({"active": ["Yes", "no", "YES", None]})
    assert str(clean_table(raw, "t").frame["active"].dtype) == "boolean"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,234", "1234"),
        ("12,5", "12.5"),
        ("1.234.567", "1234567"),
        ("€ 1 000,50", "1000.50"),
        ("-7", "-7"),
        ("abc", None),
        ("12abc", None),
    ],
)
def test_normalize_number_text(text: str, expected: str | None) -> None:
    assert normalize_number_text(text) == expected


def test_snake_case() -> None:
    assert snake_case("Order Date (UTC)") == "order_date_utc"
    assert snake_case("2024 sales") == "c_2024_sales"


def test_xlsx_sheets_become_tables_and_formulas_are_not_executed() -> None:
    workbook = openpyxl.Workbook()
    first = workbook.active
    first.title = "Sales"
    first.append(["day", "a", "b", "total"])
    first.append([date(2024, 1, 5), 2, 3, "=B2+C2"])
    second = workbook.create_sheet("Stores")
    second.append(["store", "city"])
    second.append(["S1", "Rome"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    tables = load_file("book.xlsx", buffer.getvalue())
    assert set(tables) == {"sales", "stores"}
    cleaned = clean_table(tables["sales"], "sales").frame
    assert "total" not in cleaned.columns  # formula cell has no cached value, so no data
    assert str(cleaned["day"].dtype).startswith("datetime64")


def test_corrupt_files_raise_user_facing_errors() -> None:
    with pytest.raises(UserFacingError):
        load_file("data.csv", b"\x00\x01\x02\xff\xfe\x00binary")
    with pytest.raises(UserFacingError):
        load_file("data.csv", b"")
    with pytest.raises(UserFacingError):
        load_file("book.xlsx", b"PK\x03\x04 this is not a real workbook")
    with pytest.raises(UserFacingError):
        load_file("notes.pdf", b"%PDF-1.4")
    with pytest.raises(UserFacingError):
        load_file("ragged.csv", b"a,b\n1,2,3,4,5\n6\n")


def test_size_and_row_limits() -> None:
    with pytest.raises(UserFacingError, match="MB"):
        load_file("big.csv", b"a\n" + b"1\n" * 1_100_000, Settings(_env_file=None, max_file_mb=1))
    with pytest.raises(UserFacingError, match="rows"):
        load_file("long.csv", b"a\n1\n2\n3\n", Settings(_env_file=None, max_rows=2))


def test_reserved_and_duplicate_table_names_are_made_unique() -> None:
    files = [("data.csv", b"a\n1\n"), ("data.csv", b"a\n2\n"), ("Base.csv", b"a\n3\n")]
    assert set(load_files(files)) == {"data_2", "data_3", "base_2"}


def test_repeated_values_get_the_same_result_as_single_cells() -> None:
    """Cleaning runs on distinct values; every row must still get its own correct result."""
    rows = 120
    amounts = (["1,234.50", " 7 ", "N/A", None, "$10", "(3)", "1 000,5"] * 18)[:rows]
    days = (["2024-03-01", "2024-03-02 10:30", None] * 40)[:rows]
    flags = (["Yes", "no", None, "TRUE"] * 30)[:rows]
    names = ([" a ", "b", "NULL", None] * 30)[:rows]
    ids = [str(i) for i in range(rows)]
    frame = pd.DataFrame(
        {"Id": ids, "Amount": amounts, "Day": days, "Flag": flags, "Name": names}
    )
    out = clean_table(frame, "t").frame
    first = [None if pd.isna(v) else float(v) for v in out["amount"].iloc[:7]]
    assert first == [1234.5, 7.0, None, None, 10.0, -3.0, 1000.5]
    assert out["amount"].isna().sum() == sum(v in (None, "N/A") for v in amounts)
    assert [d.date() for d in out["day"].dropna().iloc[:2]] == [date(2024, 3, 1), date(2024, 3, 2)]
    assert out["day"].isna().sum() == 40
    assert out["flag"].iloc[:4].isna().tolist() == [False, False, True, False]
    assert out["flag"].iloc[[0, 1, 3]].tolist() == [True, False, True]
    assert out["name"].iloc[:2].tolist() == ["a", "b"]
    assert out["name"].isna().sum() == 60
