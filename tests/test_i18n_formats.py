"""Spec 5.3: formatters tested against shared/format_cases.json (also used by the frontend)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from core.i18n import formats
from core.i18n.formats import (
    NBSP,
    NNBSP,
    format_compact,
    format_currency,
    format_date,
    format_decimal,
    format_integer,
    format_percent,
    month_name,
    parse_number,
    weekday_name,
)

CASES_FILE = Path(__file__).resolve().parent.parent / "shared" / "format_cases.json"
CASES = json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"]

FUNCTIONS = {
    "integer": lambda c: format_integer(c["value"], c["locale"]),
    "decimal": lambda c: format_decimal(c["value"], c["locale"], **c["args"]),
    "percent": lambda c: format_percent(c["value"], c["locale"], **c["args"]),
    "compact": lambda c: format_compact(c["value"], c["locale"], **c["args"]),
    "currency": lambda c: format_currency(c["value"], c["locale"], **c["args"]),
    "month": lambda c: month_name(c["value"], c["locale"]),
    "weekday": lambda c: weekday_name(c["value"], c["locale"]),
    "date": lambda c: format_date(c["value"], c["locale"], **c["args"]),
}


def test_the_shared_file_has_enough_cases() -> None:
    assert len(CASES) >= 80
    assert {case["fn"] for case in CASES} == set(FUNCTIONS) | {"parse"}
    assert {case["locale"] for case in CASES} == {"uz", "en"}


@pytest.mark.parametrize(
    "case", [c for c in CASES if c["fn"] != "parse"],
    ids=lambda c: f"{c['fn']}-{c['locale']}-{c['value']}-{c['args']}",
)
def test_format_cases(case: dict) -> None:
    assert FUNCTIONS[case["fn"]](case) == case["expected"]


@pytest.mark.parametrize(
    "case", [c for c in CASES if c["fn"] == "parse"], ids=lambda c: f"{c['locale']}-{c['value']!r}"
)
def test_parse_cases(case: dict) -> None:
    result = parse_number(case["value"], case["locale"])
    if case["expected"] is None:
        assert result is None
    else:
        assert result == Decimal(str(case["expected"]))


# ---------------------------------------------------------------- properties


def test_uzbek_integers_use_the_narrow_no_break_space_and_never_a_plain_space() -> None:
    text = format_integer(1234567, "uz")
    assert NNBSP in text
    assert " " not in text
    assert format_integer(1234567, "en") == "1,234,567"


def test_uzbek_units_are_glued_to_their_numbers() -> None:
    assert NBSP in format_compact(12800000, "uz")
    assert " " not in format_currency(12800000, "uz")
    assert NBSP not in format_compact(12800000, "en")


def test_the_uzbek_currency_word_uses_the_right_letter() -> None:
    text = format_currency(5000, "uz")
    assert text.endswith("so\u02bbm")
    assert "'" not in text


def test_rounding_is_half_up_not_half_even() -> None:
    assert format_decimal(0.125, "en", 2) == "0.13"
    assert format_decimal(0.135, "en", 2) == "0.14"
    assert format_decimal(2.5, "en", 0) == "3"
    assert format_decimal(-2.5, "en", 0) == "-3"


def test_negative_zero_never_appears() -> None:
    assert format_decimal(-0.0001, "uz") == "0,00"
    assert format_percent(-0.01, "en") == "0.0%"
    assert format_compact(-0.04, "en") == "0"


def test_missing_and_non_finite_values_show_a_dash() -> None:
    for value in (None, float("nan"), float("inf"), "abc", True):
        assert format_integer(value, "uz") == "\u2013"
        assert format_compact(value, "en") == "\u2013"


def test_decimal_and_integer_inputs_are_accepted() -> None:
    assert format_decimal(Decimal("1234.565"), "en") == "1,234.57"
    assert format_integer("1234", "en") == "1,234"
    assert format_integer(10**15, "en") == "1,000,000,000,000,000"


def test_unsupported_locale_or_currency_or_style_is_an_error() -> None:
    with pytest.raises(ValueError):
        format_integer(1, "ru")
    with pytest.raises(ValueError):
        format_currency(1, "uz", "GBP")
    with pytest.raises(ValueError):
        format_date("2026-01-01", "uz", "weird")
    with pytest.raises(ValueError):
        month_name(13, "uz")
    with pytest.raises(ValueError):
        weekday_name(7, "en")


def test_month_labels_are_lowercase_in_uzbek_and_capitalised_in_english() -> None:
    assert all(name == name.lower() for name in formats.MONTHS["uz"])
    assert all(name[0].isupper() for name in formats.MONTHS["en"])
    assert month_name(8, "en", short=True) == "Aug"
    assert month_name(8, "uz", short=True) == "avgust"


def test_dates_accept_date_objects_and_datetimes() -> None:
    from datetime import date, datetime

    assert format_date(date(2026, 8, 12), "uz") == "2026-yil 12-avgust"
    assert format_date(datetime(2026, 8, 12, 23, 59), "en") == "12 Aug 2026"


@pytest.mark.parametrize("locale", ["uz", "en"])
@pytest.mark.parametrize("value", [0, 7, 1234, 1234567, -98765, 12.5, 1234.56, -0.5])
def test_numbers_round_trip_through_the_parser(locale: str, value: float) -> None:
    assert parse_number(format_decimal(value, locale, 2), locale) == Decimal(str(value)).quantize(
        Decimal("0.01")
    )
    assert parse_number(format_integer(round(value), locale), locale) == round(value)


@pytest.mark.parametrize("locale", ["uz", "en"])
@pytest.mark.parametrize("value", [5000, 12_800_000, 1_200_000_000, 3_500])
def test_compact_and_currency_texts_parse_back(locale: str, value: int) -> None:
    assert parse_number(format_compact(value, locale), locale) == value
    assert parse_number(format_currency(value, locale, "UZS"), locale) == value
    assert parse_number(format_currency(value, locale, "USD"), locale) == value


def test_percent_text_parses_to_the_percent_number() -> None:
    assert parse_number(format_percent(8.2, "uz"), "uz") == Decimal("8.2")
    assert parse_number(format_percent(8.2, "en"), "en") == Decimal("8.2")


def test_the_parser_never_raises_on_garbage() -> None:
    for text in ("", " ", "--5", "1..2", "1,,2", "e10", "NaN", "inf", "1e5", "%", "mln", "Ù¡Ù¢Ù£"):
        parse_number(text, "uz")
        parse_number(text, "en")
    assert parse_number(None, "uz") is None  # type: ignore[arg-type]

