"""Detect personal data columns by name hints and by value patterns (no external libraries).

Column names decide first. Value patterns (email, phone, card number with Luhn check) are
tested on text columns only, on a sample of up to 1,000 values. Columns named ``*_id`` are
skipped by the value rules because identifiers often look like phone numbers.
"""

from __future__ import annotations

import re

import pandas as pd

SAMPLE_SIZE = 1000
MATCH_SHARE = 0.30
NAME_HINTS = (
    "name", "fullname", "full_name", "first_name", "last_name", "phone", "mobile", "email",
    "card", "pan", "passport", "ssn", "national_id", "inn", "pinfl",
)
# "name" alone is too common ("product_name", "store_name"), so it only counts for people.
PERSON_PREFIXES = ("customer", "client", "person", "contact", "user", "employee", "holder", "owner")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[\d\s().-]{7,25}$")
CARD_RE = re.compile(r"^[\d\s-]{13,23}$")


def luhn_valid(digits: str) -> bool:
    """Return True if a digit string passes the Luhn checksum."""
    total = 0
    for position, char in enumerate(reversed(digits)):
        value = int(char)
        if position % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def looks_like_card(text: str) -> bool:
    """13-19 digits (spaces and dashes allowed) with a valid Luhn checksum."""
    if not CARD_RE.match(text):
        return False
    digits = re.sub(r"\D", "", text)
    return 13 <= len(digits) <= 19 and luhn_valid(digits)


def looks_like_phone(text: str) -> bool:
    """7-15 digits with optional + ( ) - . and spaces; plain digit strings do not count."""
    if not PHONE_RE.match(text):
        return False
    digits = re.sub(r"\D", "", text)
    if not 7 <= len(digits) <= 15:
        return False
    return bool(re.search(r"[+\s().-]", text))


def name_hint(column: str) -> str | None:
    """Return the matching hint when a column name suggests personal data."""
    name = column.lower()
    for hint in NAME_HINTS:
        if name == hint or name.startswith(hint + "_") or name.endswith("_" + hint):
            if hint == "name" and name != "name" and not name.startswith(PERSON_PREFIXES):
                continue
            return hint
    return None


def value_pattern(values: pd.Series) -> str | None:
    """Return a description when at least 30% of the sample matches a personal-data pattern."""
    sample = values.dropna().astype(str).head(SAMPLE_SIZE)
    if sample.empty:
        return None
    checks = (
        ("email addresses", lambda t: bool(EMAIL_RE.match(t))),
        ("phone numbers", looks_like_phone),
        ("card numbers", looks_like_card),
    )
    for label, check in checks:
        share = float(sample.map(check).mean())
        if share >= MATCH_SHARE:
            return f"{share:.0%} of sampled values look like {label}"
    return None


def is_identifier(column: str) -> bool:
    """Identifier columns are excluded from the value rules."""
    name = column.lower()
    return name == "id" or name.endswith("_id")


def find_pii_columns(frame: pd.DataFrame) -> dict[str, str]:
    """Return ``{column: reason}`` for every column that looks like personal data."""
    found: dict[str, str] = {}
    for column in frame.columns:
        hint = name_hint(str(column))
        if hint:
            found[str(column)] = f"column name suggests personal data ('{hint}')"
            continue
        if is_identifier(str(column)) or frame[column].dtype != object:
            continue
        reason = value_pattern(frame[column])
        if reason:
            found[str(column)] = reason
    return found
