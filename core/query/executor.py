"""Run validated SQL on DuckDB with a row limit and a hard time limit."""

from __future__ import annotations

import hashlib
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import duckdb

from core.config import Settings, get_settings
from core.errors import QueryTimeoutError, ValidationError
from core.models import QueryResult
from core.query.sql_validator import validate_and_explain

INTERRUPT_GRACE_S = 2.0


def to_plain_value(value: Any) -> Any:
    """Convert DuckDB values to types that are easy to show and serialize."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return value


def run_with_timeout(
    con: duckdb.DuckDBPyConnection, sql: str, timeout_s: float
) -> tuple[list[str], list[tuple]]:
    """Run a statement in a worker thread; interrupt it when the time limit is exceeded."""
    cursor = con.cursor()
    outcome: dict[str, Any] = {}

    def work() -> None:
        try:
            cursor.execute(sql)
            outcome["columns"] = [d[0] for d in cursor.description]
            outcome["rows"] = cursor.fetchall()
        except Exception as exc:  # noqa: BLE001 - reported to the caller below
            outcome["error"] = exc

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        cursor.interrupt()
        worker.join(INTERRUPT_GRACE_S)
        raise QueryTimeoutError(
            f"The query took longer than {timeout_s:g} seconds and was stopped. "
            "Try a narrower question or a smaller date range."
        )
    cursor.close()
    if "error" in outcome:
        error = outcome["error"]
        if isinstance(error, duckdb.Error):
            raise ValidationError(str(error)[:600]) from error
        raise error
    return outcome["columns"], outcome["rows"]


def execute_query(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    settings: Settings | None = None,
    metric_ids: list[str] | None = None,
) -> QueryResult:
    """Validate, check and run a query. Raises ``ValidationError`` or ``QueryTimeoutError``."""
    settings = settings or get_settings()
    normalized = validate_and_explain(con, sql)
    limit = settings.result_row_limit
    wrapped = f"SELECT * FROM ({normalized}) LIMIT {limit}"
    started = time.perf_counter()
    columns, rows = run_with_timeout(con, wrapped, settings.query_timeout_s)
    runtime_ms = (time.perf_counter() - started) * 1000
    warnings = [f"Only the first {limit:,} rows are shown."] if len(rows) >= limit else []
    return QueryResult(
        sql=normalized,
        columns=columns,
        rows=[[to_plain_value(v) for v in row] for row in rows],
        row_count=len(rows),
        runtime_ms=round(runtime_ms, 1),
        metric_ids=list(metric_ids or []),
        warnings=warnings,
    )


def format_for_hash(value: Any) -> str:
    """Stringify a value so that 3, 3.0 and Decimal('3') hash the same (floats to 4 decimals)."""
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, int | float | Decimal):
        return f"{round(float(value), 4):.4f}"
    return str(value)


def result_hash(rows: list[list[Any]]) -> str:
    """Order-insensitive SHA-256 of a result set (column names are ignored)."""
    lines = sorted("\x1f".join(format_for_hash(v) for v in row) for row in rows)
    return hashlib.sha256("\x1e".join(lines).encode("utf-8")).hexdigest()
