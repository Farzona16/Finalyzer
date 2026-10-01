"""Clean raw text tables: names, whitespace, empty tokens, numbers, dates, booleans.

Every change is reported as a ``CleaningAction``. The input DataFrame is never modified.
Exact duplicate rows are reported but only removed when the caller asks for it.

Text operations are slow in pandas (one Python call per cell), so they run on the distinct
values of a column only and the result is spread back to the rows. Real tables repeat their
values a lot, which makes this several times faster on a million rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from core.ingest.loader import snake_case, unique_name
from core.models import CleaningAction

EMPTY_TOKENS = {"", "na", "n/a", "null", "-"}
BOOLEAN_TOKENS = {"true", "false", "yes", "no"}
MAX_BOOLEAN_VARIANTS = 12  # "true", "True", "TRUE", "yes", ... a column with more is not boolean
CURRENCY_SYMBOLS = "$€£¥₽₸"
SIMPLE_NUMBER = r"[+-]?\d+(\.\d+)?"
# Values that look like numbers but are identifiers: leading zeros, phone numbers, card numbers.
IDENTIFIER_LIKE = r"^[+-]?0\d|^\+\d{7,}$|^\d{13,}$"
THOUSANDS_COMMA = re.compile(r"^\d{1,3}(,\d{3})+(\.\d+)?$")
THOUSANDS_DOT = re.compile(r"^\d{1,3}(\.\d{3})+$")
DATE_LIKE = r"^\d{1,4}[-./]\d{1,2}[-./]\d{1,4}"
NUMERIC_SHARE = 0.95


@dataclass
class CleanResult:
    """Cleaned table plus everything that was done to it."""

    frame: pd.DataFrame
    actions: list[CleaningAction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class Distinct:
    """A column seen through its distinct values.

    ``codes`` holds, for every row, the position of its value in ``values`` (-1 for a missing
    cell); ``counts`` says how many rows each distinct value has.
    """

    codes: np.ndarray
    values: pd.Series
    counts: np.ndarray
    index: pd.Index

    @classmethod
    def of(cls, column: pd.Series) -> Distinct:
        """Factorize a column of text."""
        codes, uniques = pd.factorize(column.astype("object"))
        values = pd.Series(uniques, dtype="object")
        counts = np.bincount(codes[codes >= 0], minlength=len(values))
        return cls(codes, values, counts, column.index)

    @property
    def present(self) -> int:
        """Number of rows with a value."""
        return int(self.counts.sum())

    def rows(self, flags: pd.Series | np.ndarray) -> int:
        """Number of rows whose distinct value has its flag set."""
        return int(self.counts[np.asarray(flags, dtype=bool)].sum())

    def spread(self, per_value: pd.Series) -> pd.Series:
        """One result per row, taken from the distinct value of that row (missing stays missing)."""
        if per_value.empty:
            return pd.Series(np.nan, index=self.index, dtype="object")
        rows = per_value.reset_index(drop=True).iloc[np.maximum(self.codes, 0)]
        rows.index = self.index
        return rows.where(self.codes >= 0)


def normalize_number_text(text: str) -> str | None:
    """Turn a formatted number ("1 234,50", "$1,234.5", "(12)") into plain ``-1234.5`` text.

    Returns ``None`` when the text is not a number. A lone "1,234" is read as one thousand
    two hundred thirty-four (US style); "12,5" is read as 12.5.
    """
    s = text.strip()
    negative = s.startswith("(") and s.endswith(")")
    s = s[1:-1] if negative else s
    s = re.sub(r"[\s\u00a0\u202f']", "", s)
    s = "".join(ch for ch in s if ch not in CURRENCY_SYMBOLS)
    if s[:1] in ("+", "-"):
        negative = negative or s[0] == "-"
        s = s[1:]
    sign = "-" if negative else ""
    if "," in s and "." in s:
        decimal_is_comma = s.rfind(",") > s.rfind(".")
        s = s.replace(".", "").replace(",", ".") if decimal_is_comma else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", "") if THOUSANDS_COMMA.match(s) else s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "") if THOUSANDS_DOT.match(s) else ""
    if not re.fullmatch(r"\d+(\.\d+)?", s):
        return None
    return sign + s


def parse_distinct_numbers(texts: pd.Series) -> tuple[pd.Series, np.ndarray]:
    """Parse distinct texts to floats (NaN when not a number).

    Also returns a flag per text: True when it is a number that needed more than a plain
    ``123.45`` parse (separators, currency symbols, decimal commas).
    """
    result = pd.Series(np.nan, index=texts.index, dtype="float64")
    formatted = np.zeros(len(texts), dtype=bool)
    identifier = texts.str.contains(IDENTIFIER_LIKE, regex=True)
    simple = texts.str.fullmatch(SIMPLE_NUMBER) & ~identifier
    result[simple] = pd.to_numeric(texts[simple])
    rest = texts[~simple & ~identifier]
    if rest.empty:
        return result, formatted
    parsed = rest.map({text: normalize_number_text(text) for text in rest})
    valid = parsed.notna()
    result[valid[valid].index] = pd.to_numeric(parsed[valid])
    formatted[valid[valid].index.to_numpy()] = True
    return result, formatted


def try_numeric(
    view: Distinct, table: str, column: str
) -> tuple[pd.Series, list[CleaningAction], list[str]] | None:
    """Convert a text column to numbers when at least 95% of its values parse."""
    if view.present == 0:
        return None
    numbers, formatted = parse_distinct_numbers(view.values)
    good = view.rows(numbers.notna())
    if good < NUMERIC_SHARE * view.present:
        return None
    actions: list[CleaningAction] = []
    warnings: list[str] = []
    if view.rows(formatted):
        actions.append(
            CleaningAction(
                table=table, column=column, action="parsed_formatted_numbers",
                count=view.rows(formatted),
                detail="Removed thousand separators / currency symbols / decimal commas.",
            )
        )
    lost = view.present - good
    if lost:
        actions.append(
            CleaningAction(
                table=table, column=column, action="unparseable_numbers_to_null", count=lost,
                detail="Values that are not numbers were set to empty.",
            )
        )
        warnings.append(f"Column '{column}' in '{table}': {lost} non-numeric values were emptied.")
    full = view.spread(numbers).astype("float64")
    present = numbers.dropna()
    is_whole = bool((present % 1 == 0).all()) and bool(present.abs().max() < 2**53)
    if is_whole:
        full = full.astype("Int64") if full.isna().any() else full.astype("int64")
    return full, actions, warnings


def parse_slash_dates(remaining: pd.Series) -> tuple[pd.Series, bool]:
    """Parse ``a/b/yyyy`` dates as month/day or day/month, whichever fits the column better.

    Returns the parsed dates and whether the choice was ambiguous (both fit equally well).
    """
    us = pd.to_datetime(remaining, format="%m/%d/%Y", errors="coerce")
    eu = pd.to_datetime(remaining, format="%d/%m/%Y", errors="coerce")
    if us.notna().sum() > eu.notna().sum():
        return us, False
    return eu, bool(us.notna().any() and us.notna().sum() == eu.notna().sum())


def parse_date_values(texts: pd.Series) -> tuple[pd.Series, list[str]]:
    """Parse date text with several formats. Returns dates and warning fragments."""
    date_part = texts.str.split(r"[ T]", n=1, regex=True).str[0]
    result = pd.Series(pd.NaT, index=texts.index, dtype="datetime64[ns]")
    formats_used = 0
    notes: list[str] = []
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y"):
        todo = result.isna()
        parsed = pd.to_datetime(date_part[todo], format=fmt, errors="coerce")
        if parsed.notna().any():
            result[parsed.index] = parsed
            formats_used += 1
    todo = result.isna() & date_part.str.contains("/", regex=False)
    if todo.any():
        parsed, ambiguous = parse_slash_dates(date_part[todo])
        if parsed.notna().any():
            result[parsed.index] = parsed
            formats_used += 1
        if ambiguous:
            notes.append("dates such as 03/04/2024 are ambiguous and were read as day/month/year")
    if formats_used > 1:
        notes.append("the column mixes several date formats; each value used the format that fit")
    return result, notes


def try_dates(
    view: Distinct, table: str, column: str
) -> tuple[pd.Series, list[CleaningAction], list[str]] | None:
    """Convert a text column to dates when at least 95% of its values parse."""
    if view.present == 0:
        return None
    if view.values.head(1000).str.match(DATE_LIKE).mean() < NUMERIC_SHARE:
        return None
    parsed, notes = parse_date_values(view.values)
    good = view.rows(parsed.notna())
    if good < NUMERIC_SHARE * view.present:
        return None
    actions: list[CleaningAction] = []
    warnings = [f"Column '{column}' in '{table}': {note}." for note in notes]
    lost = view.present - good
    if lost:
        actions.append(
            CleaningAction(
                table=table, column=column, action="unparseable_dates_to_null", count=lost,
                detail="Values that are not dates were set to empty.",
            )
        )
    with_time = view.rows(view.values.str.contains(r"[ T]\d{1,2}:\d{2}", regex=True))
    if with_time:
        actions.append(
            CleaningAction(
                table=table, column=column, action="time_of_day_dropped", count=with_time,
                detail="Only the date part is used.",
            )
        )
    return view.spread(parsed.dt.normalize()).astype("datetime64[ns]"), actions, warnings


def try_boolean(view: Distinct, index: pd.Index) -> pd.Series | None:
    """Convert true/false/yes/no columns to booleans."""
    if view.present == 0 or len(view.values) > MAX_BOOLEAN_VARIANTS:
        return None
    lowered = view.values.str.lower()
    if not lowered.isin(BOOLEAN_TOKENS).all():
        return None
    truth = lowered.isin({"true", "yes"}).astype("object")
    return view.spread(truth).astype("boolean").reindex(index)


def clean_column_names(
    frame: pd.DataFrame, table: str
) -> tuple[pd.DataFrame, list[CleaningAction]]:
    """Strip and snake_case column names; add suffixes to duplicates."""
    taken: set[str] = set()
    names: list[str] = []
    actions: list[CleaningAction] = []
    for original in frame.columns:
        name = unique_name(snake_case(str(original)), taken)
        taken.add(name)
        names.append(name)
        if name != str(original):
            actions.append(
                CleaningAction(
                    table=table, column=name, action="renamed_column", count=1,
                    detail=f"'{original}' -> '{name}'",
                )
            )
    renamed = frame.copy()
    renamed.columns = names
    return renamed, actions


def clean_text_column(column: pd.Series, table: str, name: str) -> tuple[pd.Series, list]:
    """Trim one column and turn empty-like tokens into nulls."""
    view = Distinct.of(column)
    trimmed = view.values.str.strip()
    empty = trimmed.str.lower().isin(EMPTY_TOKENS)
    changed = view.rows(trimmed.notna() & (trimmed != view.values))
    nulled = view.rows(empty & (trimmed != ""))
    cleaned = view.spread(trimmed.mask(empty, None))
    actions = []
    if changed:
        actions.append(
            CleaningAction(table=table, column=name, action="trimmed_whitespace",
                           count=changed, detail="Leading/trailing spaces removed.")
        )
    if nulled:
        actions.append(
            CleaningAction(table=table, column=name, action="empty_tokens_to_null",
                           count=nulled, detail="Values like NA, N/A, null, - were emptied.")
        )
    return cleaned, actions


def clean_text_cells(
    frame: pd.DataFrame, table: str
) -> tuple[pd.DataFrame, list[CleaningAction]]:
    """Trim strings and turn empty-like tokens into nulls."""
    actions: list[CleaningAction] = []
    result = frame.copy()
    for column in result.columns:
        result[column], column_actions = clean_text_column(result[column], table, column)
        actions += column_actions
    return result, actions


def drop_empty(frame: pd.DataFrame, table: str) -> tuple[pd.DataFrame, list[CleaningAction]]:
    """Remove columns and rows that contain no values at all."""
    actions: list[CleaningAction] = []
    empty_columns = [c for c in frame.columns if frame[c].isna().all()]
    if empty_columns:
        actions.append(
            CleaningAction(table=table, column=", ".join(empty_columns),
                           action="dropped_empty_columns", count=len(empty_columns))
        )
    result = frame.drop(columns=empty_columns)
    empty_rows = result.isna().all(axis=1)
    if empty_rows.any():
        actions.append(
            CleaningAction(table=table, column="*", action="dropped_empty_rows",
                           count=int(empty_rows.sum()))
        )
        result = result[~empty_rows].reset_index(drop=True)
    return result, actions


def convert_types(frame: pd.DataFrame, table: str) -> CleanResult:
    """Convert every text column to boolean, number or date when it clearly is one."""
    result = frame.copy()
    actions: list[CleaningAction] = []
    warnings: list[str] = []
    for column in list(result.columns):
        values = result[column]
        if values.dtype != object:
            continue
        view = Distinct.of(values)
        boolean = try_boolean(view, values.index)
        if boolean is not None:
            result[column] = boolean
            continue
        for converter in (try_numeric, try_dates):
            converted = converter(view, table, column)
            if converted is not None:
                result[column], new_actions, new_warnings = converted
                actions += new_actions
                warnings += new_warnings
                break
    return CleanResult(result, actions, warnings)


def handle_duplicates(
    frame: pd.DataFrame, table: str, remove: bool
) -> tuple[pd.DataFrame, list[CleaningAction]]:
    """Report exact duplicate rows; remove them only if ``remove`` is true."""
    count = int(frame.duplicated().sum())
    if count == 0:
        return frame, []
    if remove:
        action = CleaningAction(table=table, column="*", action="duplicate_rows_removed",
                                count=count, detail="Exact duplicate rows were removed.")
        return frame.drop_duplicates().reset_index(drop=True), [action]
    action = CleaningAction(table=table, column="*", action="duplicate_rows_found", count=count,
                            detail="Exact duplicate rows were found and kept.")
    return frame, [action]


def clean_table(raw: pd.DataFrame, table: str, remove_duplicates: bool = False) -> CleanResult:
    """Run every cleaning step on one raw table."""
    frame, actions = clean_column_names(raw, table)
    frame, more = clean_text_cells(frame, table)
    actions += more
    frame, more = drop_empty(frame, table)
    actions += more
    converted = convert_types(frame, table)
    frame, more = handle_duplicates(converted.frame, table, remove_duplicates)
    return CleanResult(frame, actions + converted.actions + more, converted.warnings)


def clean_tables(
    tables: dict[str, pd.DataFrame], remove_duplicates: bool = False
) -> tuple[dict[str, pd.DataFrame], list[CleaningAction], list[str]]:
    """Clean several tables. Returns tables, all actions and all warnings."""
    cleaned: dict[str, pd.DataFrame] = {}
    actions: list[CleaningAction] = []
    warnings: list[str] = []
    for name, raw in tables.items():
        result = clean_table(raw, name, remove_duplicates)
        cleaned[name] = result.frame
        actions += result.actions
        warnings += result.warnings
    return cleaned, actions, warnings
