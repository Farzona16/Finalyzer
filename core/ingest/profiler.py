"""Column profiling with DuckDB: type, null share, distinct count, categories, range."""

from __future__ import annotations

import duckdb
import pandas as pd

from core.models import ColumnInfo, DType

MAX_CATEGORIES = 30
INTEGER_TYPES = {
    "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER",
    "UBIGINT", "UHUGEINT",
}
FLOAT_TYPES = {"FLOAT", "DOUBLE", "REAL"}


def quote_identifier(name: str) -> str:
    """Quote a column or table name for DuckDB."""
    return '"' + name.replace('"', '""') + '"'


def dtype_from_duckdb(type_name: str) -> DType:
    """Map a DuckDB type name to one of our five dtypes."""
    upper = type_name.upper()
    if upper in INTEGER_TYPES:
        return "integer"
    if upper in FLOAT_TYPES or upper.startswith("DECIMAL"):
        return "float"
    if upper == "BOOLEAN":
        return "boolean"
    if upper == "DATE" or upper.startswith("TIMESTAMP"):
        return "date"
    return "text"


def format_bound(value: object) -> float | str | None:
    """Turn a min/max value into a float or an ISO string."""
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def column_statistics(
    con: duckdb.DuckDBPyConnection, view: str, name: str, dtype: DType, row_count: int
) -> ColumnInfo:
    """Compute the profile of one column."""
    col = quote_identifier(name)
    stats = con.execute(
        f"SELECT COUNT({col}), COUNT(DISTINCT {col}), MIN({col}), MAX({col}) "
        f"FROM {quote_identifier(view)}"
    ).fetchone()
    non_null, distinct = int(stats[0]), int(stats[1])
    null_pct = 100.0 * (row_count - non_null) / row_count if row_count else 0.0
    info = ColumnInfo(name=name, dtype=dtype, null_pct=round(null_pct, 2), distinct=distinct)
    if dtype in ("integer", "float", "date"):
        info.min_value, info.max_value = format_bound(stats[2]), format_bound(stats[3])
    if dtype == "text" and 0 < distinct <= MAX_CATEGORIES:
        rows = con.execute(
            f"SELECT {col}, COUNT(*) AS n FROM {quote_identifier(view)} "
            f"WHERE {col} IS NOT NULL GROUP BY {col} ORDER BY n DESC, {col} LIMIT {MAX_CATEGORIES}"
        ).fetchall()
        info.categories = [str(r[0]) for r in rows]
    return info


def profile_view(con: duckdb.DuckDBPyConnection, view: str) -> list[ColumnInfo]:
    """Profile every column of a table or view."""
    described = con.execute(f"DESCRIBE {quote_identifier(view)}").fetchall()
    row_count = int(con.execute(f"SELECT COUNT(*) FROM {quote_identifier(view)}").fetchone()[0])
    return [
        column_statistics(con, view, str(row[0]), dtype_from_duckdb(str(row[1])), row_count)
        for row in described
    ]


def profile_frame(frame: pd.DataFrame) -> list[ColumnInfo]:
    """Profile a DataFrame (uses a throw-away DuckDB connection)."""
    con = duckdb.connect(":memory:")
    try:
        con.register("frame_to_profile", frame)
        return profile_view(con, "frame_to_profile")
    finally:
        con.close()
