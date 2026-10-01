"""SQL guardrails for model-written queries.

A query is accepted only if it parses as exactly one read-only statement (``SELECT``, with
CTEs and set operations allowed), touches no table except ``data`` (or its own CTEs), and uses
no file, URL, catalog or environment functions. The accepted query is returned in a
normalized form (parsed and re-printed, comments removed), and that text is what gets executed.
"""

from __future__ import annotations

import logging

import duckdb
import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from core.errors import ValidationError

# sqlglot logs a warning for every statement it cannot parse; those are rejected anyway.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

MAX_SQL_LENGTH = 5000
ALLOWED_TABLE = "data"
BLOCKED_FUNCTIONS = frozenset(
    {
        "read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto", "read_text",
        "read_blob", "read_ndjson", "glob", "sniff_csv", "query", "query_table", "getenv",
        "current_setting", "httpfs", "load_extension", "nextval", "currval", "setseed",
        "st_read", "list_files", "checkpoint", "force_checkpoint", "which_secret",
    }
)
BLOCKED_PREFIXES = (
    "pragma_", "duckdb_", "read_", "parquet_", "iceberg_", "delta_", "sqlite_", "postgres_",
    "mysql_", "scan_",
)
FORBIDDEN_NODE_NAMES = (
    "Insert", "Update", "Delete", "Create", "Drop", "Alter", "AlterTable", "Merge", "Command",
    "Copy", "Attach", "Detach", "Pragma", "Set", "Install", "Into", "Use", "TruncateTable",
    "Transaction", "Commit", "Rollback", "Describe", "Show", "Export", "Grant",
)
FORBIDDEN_NODES = tuple(
    getattr(exp, name) for name in FORBIDDEN_NODE_NAMES if hasattr(exp, name)
)
ROOT_TYPES = (exp.Select, getattr(exp, "SetOperation", exp.Union))
ALLOWED_TABLE_FUNCTIONS = (exp.GenerateSeries, exp.Unnest)


def parse_single_statement(sql: str) -> exp.Expression:
    """Parse the text and require exactly one statement."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except SqlglotError as exc:
        raise ValidationError(f"The SQL could not be parsed: {str(exc).splitlines()[0]}") from exc
    if len(statements) != 1:
        raise ValidationError("Exactly one SQL statement is allowed.")
    return statements[0]


def unwrap_parentheses(tree: exp.Expression) -> exp.Expression:
    """Remove outer parentheses: ``(SELECT 1)`` is a Subquery node."""
    while isinstance(tree, exp.Subquery) and not tree.args.get("alias"):
        tree = tree.this
    return tree


def check_root(tree: exp.Expression) -> None:
    """Only SELECT statements (and set operations of SELECTs) are allowed."""
    if not isinstance(unwrap_parentheses(tree), ROOT_TYPES):
        raise ValidationError("Only SELECT queries are allowed.")


def check_forbidden_nodes(tree: exp.Expression) -> None:
    """Reject DDL, DML, commands and other statement kinds anywhere in the tree."""
    node = tree.find(*FORBIDDEN_NODES)
    if node is not None:
        raise ValidationError(f"{type(node).__name__.upper()} is not allowed.")


def cte_names(tree: exp.Expression) -> set[str]:
    """Names of all common table expressions defined in the query."""
    return {cte.alias.lower() for cte in tree.find_all(exp.CTE) if cte.alias}


def check_tables(tree: exp.Expression) -> None:
    """The only physical table is ``data``; CTE names are also fine; no qualifiers."""
    allowed = {ALLOWED_TABLE} | cte_names(tree)
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            if isinstance(table.this, ALLOWED_TABLE_FUNCTIONS):
                continue
            raise ValidationError("Table functions are not allowed.")
        if table.args.get("db") or table.args.get("catalog"):
            raise ValidationError("Schema or catalog qualifiers are not allowed.")
        if table.name.lower() not in allowed:
            raise ValidationError(f"Table '{table.name}' is not available. Use only 'data'.")
    for column in tree.find_all(exp.Column):
        if column.args.get("db") or column.args.get("catalog"):
            raise ValidationError("Schema or catalog qualifiers are not allowed.")


def function_name(node: exp.Func) -> str:
    """Lowercase name of a function call."""
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    return node.sql_name().lower()


def check_functions(tree: exp.Expression) -> None:
    """Reject functions that read files, URLs, settings or the catalog."""
    for node in tree.find_all(exp.Func):
        name = function_name(node)
        if name in BLOCKED_FUNCTIONS or name.startswith(BLOCKED_PREFIXES):
            raise ValidationError(f"The function {name}() is not allowed.")


def validate_sql(sql: str) -> str:
    """Return the normalized SQL or raise ``ValidationError`` with the reason."""
    text = (sql or "").strip()
    if not text:
        raise ValidationError("The SQL is empty.")
    if len(text) > MAX_SQL_LENGTH:
        raise ValidationError(f"The SQL is longer than {MAX_SQL_LENGTH} characters.")
    tree = parse_single_statement(text)
    check_root(tree)
    check_forbidden_nodes(tree)
    check_tables(tree)
    check_functions(tree)
    return tree.sql(dialect="duckdb", comments=False)


def explain_check(con: duckdb.DuckDBPyConnection, sql: str) -> None:
    """Run ``EXPLAIN`` so that unknown columns and type errors show up before execution."""
    cursor = con.cursor()
    try:
        cursor.execute(f"EXPLAIN {sql}")
    except duckdb.Error as exc:
        raise ValidationError(str(exc)[:600]) from exc
    finally:
        cursor.close()


def validate_and_explain(con: duckdb.DuckDBPyConnection, sql: str) -> str:
    """Validate the SQL, then check it against the real schema. Returns the normalized SQL."""
    normalized = validate_sql(sql)
    explain_check(con, normalized)
    return normalized
