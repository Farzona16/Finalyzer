"""Read CSV and XLSX files into raw text tables.

Every cell is returned as text (or ``None``) so that CSV and Excel files go through the
same cleaning code. Formulas are never executed: Excel files are read with cached values only.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from collections import Counter
from datetime import date, datetime, time
from pathlib import Path

import pandas as pd

from core.config import Settings, get_settings
from core.errors import UserFacingError
from core.runtime.profile import load_limits
from core.runtime.sampling import maybe_sample_frame
from core.runtime.watchdog import check_memory

ENCODINGS = ("utf-8", "utf-8-sig", "cp1251", "latin-1")
DELIMITERS = (",", ";", "\t", "|")
CSV_SUFFIXES = (".csv", ".tsv", ".txt")
EXCEL_SUFFIXES = (".xlsx", ".xlsm")
XLS_SUFFIXES = (".xls",)
PARQUET_SUFFIXES = (".parquet",)
BLOCKED_IN_FREE = (".ods", ".zip")
SUPPORTED_SUFFIXES = CSV_SUFFIXES + EXCEL_SUFFIXES + XLS_SUFFIXES + PARQUET_SUFFIXES
# "base" and "data" are the names of the flat views, so no table may use them.
PREVIEW_VIEW = "preview"  # used by the Data tab to inspect a join before it is built
RESERVED_TABLE_NAMES = frozenset({"base", "data", PREVIEW_VIEW})


def snake_case(name: str, fallback: str = "column") -> str:
    """Convert any text to a lowercase snake_case identifier."""
    text = re.sub(r"[\W_]+", "_", str(name).strip()).strip("_").lower()
    if not text:
        return fallback
    return f"c_{text}" if text[0].isdigit() else text


def unique_name(name: str, taken: set[str]) -> str:
    """Return ``name`` or ``name_2``, ``name_3`` ... so that it is not in ``taken``."""
    if name not in taken:
        return name
    counter = 2
    while f"{name}_{counter}" in taken:
        counter += 1
    return f"{name}_{counter}"


def decode_bytes(data: bytes) -> str:
    """Decode text using the first encoding that works."""
    if b"\x00" in data[:8192]:
        raise UserFacingError("This file is not a text file. Please upload a CSV or XLSX file.")
    for encoding in ENCODINGS:
        try:
            return data.decode(encoding).lstrip("\ufeff")
        except UnicodeDecodeError:
            continue
    raise UserFacingError("The file text encoding is not supported.")


def sniff_delimiter(text: str) -> str:
    """Pick the delimiter that splits most lines into the same number of fields."""
    lines = text[:200_000].splitlines()[:50]
    best, best_score = ",", (False, 0, 0)
    for delimiter in DELIMITERS:
        widths = [len(row) for row in csv.reader(lines, delimiter=delimiter) if row]
        if not widths:
            continue
        width, frequency = Counter(widths).most_common(1)[0]
        score = (width > 1, frequency, width)
        if score > best_score:
            best, best_score = delimiter, score
    return best


def read_csv_text(data: bytes) -> pd.DataFrame:
    """Parse CSV bytes into an all-text DataFrame."""
    text = decode_bytes(data)
    if not text.strip():
        raise UserFacingError("The file is empty.")
    try:
        # header=None makes pandas fail on rows that are wider than the first row, instead of
        # silently turning the extra leading fields into an index.
        raw = pd.read_csv(
            io.StringIO(text),
            sep=sniff_delimiter(text),
            dtype=str,
            keep_default_na=False,
            header=None,
        )
    except (pd.errors.ParserError, pd.errors.EmptyDataError, ValueError) as exc:
        raise UserFacingError(f"The file could not be read as a table: {exc}") from exc
    header = [str(v) for v in raw.iloc[0]]
    body = raw.iloc[1:].reset_index(drop=True)
    body.columns = header
    return body


def cell_to_text(value: object) -> str | None:
    """Convert one Excel cell value to text."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time(0) else value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def read_excel_tables(data: bytes, stem: str) -> dict[str, pd.DataFrame]:
    """Read every sheet of an Excel file (cached values only, no formulas)."""
    # Imported here so that CSV-only use does not pay the openpyxl import cost.
    import openpyxl

    try:
        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except (zipfile.BadZipFile, KeyError, OSError, ValueError) as exc:
        raise UserFacingError("The Excel file is corrupt or not a valid .xlsx file.") from exc
    tables: dict[str, pd.DataFrame] = {}
    for sheet in workbook.worksheets:
        rows = [[cell_to_text(c) for c in row] for row in sheet.iter_rows(values_only=True)]
        rows = [row for row in rows if any(cell is not None for cell in row)]
        if len(rows) < 1:
            continue
        header = [cell_to_text(h) or "" for h in rows[0]]
        body = [row + [None] * (len(header) - len(row)) for row in rows[1:]]
        name = stem if len(workbook.worksheets) == 1 else sheet.title
        tables[name] = pd.DataFrame([r[: len(header)] for r in body], columns=header, dtype=object)
    workbook.close()
    if not tables:
        raise UserFacingError("The Excel file contains no data.")
    return tables


def check_limits(tables: dict[str, pd.DataFrame], settings: Settings) -> None:
    """Refuse tables larger than the configured (and profile-capped) row limit."""
    cap = min(settings.max_rows, load_limits().max_rows)
    for name, frame in tables.items():
        if len(frame) > cap:
            raise UserFacingError(
                f"Table '{name}' has {len(frame):,} rows; the limit is {cap:,}."
            )


def read_xls_tables(data: bytes, stem: str) -> dict[str, pd.DataFrame]:
    """Read every sheet of a legacy ``.xls`` workbook (xlrd, values only)."""
    import xlrd

    try:
        book = xlrd.open_workbook(file_contents=data, formatting_info=False)
    except xlrd.XLRDError as exc:
        raise UserFacingError("The Excel file is corrupt or not a valid .xls file.") from exc
    tables: dict[str, pd.DataFrame] = {}
    for sheet in book.sheets():
        if sheet.nrows < 1:
            continue
        header = [str(sheet.cell_value(0, c) or "") for c in range(sheet.ncols)]
        body = [
            [cell_to_text(sheet.cell_value(r, c)) for c in range(sheet.ncols)]
            for r in range(1, sheet.nrows)
        ]
        name = stem if book.nsheets == 1 else sheet.name
        tables[name] = pd.DataFrame(body, columns=header, dtype=object)
    if not tables:
        raise UserFacingError("The Excel file contains no data.")
    return tables


def read_parquet_table(data: bytes, stem: str) -> pd.DataFrame:
    """Load a Parquet file through DuckDB."""
    import tempfile

    import duckdb

    from core.runtime.profile import duckdb_pragmas

    del stem
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as handle:
        handle.write(data)
        path = handle.name
    con = duckdb.connect(":memory:")
    try:
        for statement in duckdb_pragmas():
            try:
                con.execute(statement)
            except Exception:  # noqa: BLE001
                if "max_memory" not in statement:
                    raise
        con.execute(
            "CREATE TABLE parquet_in AS SELECT * FROM read_parquet(?)",
            [path],
        )
        frame = con.execute("SELECT * FROM parquet_in").df()
    except UserFacingError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise UserFacingError("The Parquet file could not be read.") from exc
    finally:
        con.close()
        Path(path).unlink(missing_ok=True)
    if frame.empty and list(frame.columns) == []:
        raise UserFacingError("The Parquet file contains no data.")
    return frame.astype(object)


def load_file(
    filename: str, data: bytes, settings: Settings | None = None
) -> dict[str, pd.DataFrame]:
    """Load one uploaded file. Returns table name -> raw text DataFrame."""
    settings = settings or get_settings()
    limits = load_limits()
    cap = min(settings.max_file_mb, limits.max_file_mb)
    if len(data) > cap * 1024 * 1024:
        raise UserFacingError(f"'{filename}' is larger than the {cap} MB limit.")
    path = Path(filename)
    suffix = path.suffix.lower()
    if suffix in BLOCKED_IN_FREE and not (limits.allow_ods or limits.allow_zip):
        raise UserFacingError(
            f"'{filename}': .ods and .zip uploads are turned off on this plan."
        )
    if suffix not in SUPPORTED_SUFFIXES:
        raise UserFacingError(
            f"'{filename}': only CSV, TSV, Excel and Parquet files are supported."
        )
    stem = snake_case(path.stem, fallback="table")
    if suffix in EXCEL_SUFFIXES:
        tables = {snake_case(k, "table"): v for k, v in read_excel_tables(data, stem).items()}
    elif suffix in XLS_SUFFIXES:
        tables = {snake_case(k, "table"): v for k, v in read_xls_tables(data, stem).items()}
    elif suffix in PARQUET_SUFFIXES:
        tables = {stem: read_parquet_table(data, stem)}
    else:
        tables = {stem: read_csv_text(data)}
    check_limits(tables, settings)
    return tables


def load_files(
    files: list[tuple[str, bytes]],
    settings: Settings | None = None,
    warnings: list[str] | None = None,
) -> dict[str, pd.DataFrame]:
    """Load several files. One corrupt file is skipped; an empty batch is an error."""
    settings = settings or get_settings()
    limits = load_limits()
    if len(files) > limits.max_files:
        raise UserFacingError(f"At most {limits.max_files} files can be loaded at once.")
    total = sum(len(data) for _, data in files)
    if total > limits.max_total_mb * 1024 * 1024:
        raise UserFacingError(
            f"The files together are larger than the {limits.max_total_mb} MB limit."
        )
    result: dict[str, pd.DataFrame] = {}
    skipped: list[str] = []
    sample_fraction = 1.0
    for filename, data in files:
        check_memory(limits)
        try:
            loaded = load_file(filename, data, settings)
        except UserFacingError as exc:
            skipped.append(f"{filename}: {exc.message}")
            continue
        for name, frame in loaded.items():
            sampled, fraction = maybe_sample_frame(frame, limits)
            sample_fraction = min(sample_fraction, fraction)
            result[unique_name(name, set(result) | RESERVED_TABLE_NAMES)] = sampled
    if warnings is not None:
        warnings.extend(skipped)
        if sample_fraction < 0.999:
            warnings.append(f"sampled:{sample_fraction:.6f}")
    if not result:
        raise UserFacingError(
            "None of the files could be read. " + (" ".join(skipped) if skipped else "")
        )
    return result


def load_folder(folder: Path, settings: Settings | None = None) -> dict[str, pd.DataFrame]:
    """Load every CSV file in a folder (used for the demo datasets)."""
    files = [(p.name, p.read_bytes()) for p in sorted(folder.glob("*.csv"))]
    if not files:
        raise UserFacingError(f"No CSV files found in {folder}.")
    return load_files(files, settings)
