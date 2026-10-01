"""Build the single flat view (decision D2).

``open_workspace`` loads cleaned tables into a locked-down in-memory DuckDB connection and
profiles them. ``build_dataset`` then creates ``base`` (fact table LEFT JOINed with its
dimensions, PII removed) and ``data`` (= ``base``; the filters of P5 rebuild it).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field

import duckdb
import pandas as pd

from core.config import Settings, get_settings
from core.errors import ValidationError
from core.ingest.cleaner import clean_tables
from core.ingest.loader import RESERVED_TABLE_NAMES
from core.ingest.pii import find_pii_columns
from core.ingest.profiler import profile_view, quote_identifier
from core.ingest.relationships import propose_relationships
from core.ingest.roles import ROLE_SYNONYMS, detect_roles, type_fits
from core.models import CleaningAction, ColumnInfo, DatasetModel, Relationship


@dataclass
class Workspace:
    """Uploaded, cleaned and profiled tables inside one DuckDB connection."""

    con: duckdb.DuckDBPyConnection
    profiles: dict[str, list[ColumnInfo]]
    row_counts: dict[str, int]
    pii: dict[str, dict[str, str]]
    cleaning_report: list[CleaningAction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def create_connection(settings: Settings) -> duckdb.DuckDBPyConnection:
    """Create an in-memory DuckDB connection with external access disabled."""
    con = duckdb.connect(":memory:")
    con.execute(f"SET memory_limit='{settings.duckdb_memory_limit}'")
    con.execute("SET enable_external_access=false")
    return con


def materialize_table(con: duckdb.DuckDBPyConnection, name: str, frame: pd.DataFrame) -> None:
    """Copy a DataFrame into a DuckDB table; datetime columns become DATE."""
    date_columns = [c for c in frame.columns if str(frame[c].dtype).startswith("datetime64")]
    casts = ", ".join(f"CAST({quote_identifier(c)} AS DATE) AS {quote_identifier(c)}"
                      for c in date_columns)
    select = f"* REPLACE ({casts})" if casts else "*"
    con.register("incoming_frame", frame)
    try:
        con.execute(f"CREATE TABLE {quote_identifier(name)} AS SELECT {select} FROM incoming_frame")
    finally:
        con.unregister("incoming_frame")


def open_workspace(
    tables: dict[str, pd.DataFrame],
    cleaning_report: list[CleaningAction] | None = None,
    warnings: list[str] | None = None,
    settings: Settings | None = None,
) -> Workspace:
    """Load cleaned tables, profile them, flag PII columns and lock the connection."""
    settings = settings or get_settings()
    for name in tables:
        if name in RESERVED_TABLE_NAMES:
            raise ValidationError(f"'{name}' is a reserved table name.")
    con = create_connection(settings)
    pii: dict[str, dict[str, str]] = {}
    for name, frame in tables.items():
        materialize_table(con, name, frame)
        pii[name] = find_pii_columns(frame)
    profiles = {name: profile_view(con, name) for name in tables}
    for name, columns in profiles.items():
        for column in columns:
            if column.name in pii[name]:
                column.is_pii, column.pii_reason = True, pii[name][column.name]
    con.execute("SET lock_configuration=true")
    return Workspace(
        con=con,
        profiles=profiles,
        row_counts={name: len(frame) for name, frame in tables.items()},
        pii=pii,
        cleaning_report=list(cleaning_report or []),
        warnings=list(warnings or []),
    )


def workspace_from_raw(
    raw_tables: dict[str, pd.DataFrame],
    remove_duplicates: bool = False,
    settings: Settings | None = None,
) -> Workspace:
    """Clean raw text tables and open a workspace in one step."""
    cleaned, actions, warnings = clean_tables(raw_tables, remove_duplicates)
    return open_workspace(cleaned, actions, warnings, settings)


def pick_fact_table(workspace: Workspace) -> str:
    """The table with a date column and the most rows (fallback: the largest table)."""
    with_dates = [
        name for name, columns in workspace.profiles.items()
        if any(c.dtype == "date" and not c.is_pii for c in columns)
    ]
    pool = with_dates or list(workspace.profiles)
    return max(pool, key=lambda name: workspace.row_counts[name])


def propose(workspace: Workspace, fact_table: str) -> list[Relationship]:
    """Propose joins from the fact table to the other tables."""
    return propose_relationships(
        workspace.con, fact_table, workspace.profiles, workspace.row_counts
    )


def check_relationship(workspace: Workspace, fact_table: str, rel: Relationship) -> None:
    """Refuse joins that reference unknown columns, mix types or would duplicate fact rows."""
    fact_cols = {c.name: c for c in workspace.profiles[fact_table]}
    dim_cols = {c.name: c for c in workspace.profiles.get(rel.dim_table, [])}
    if rel.fact_col not in fact_cols or rel.dim_col not in dim_cols:
        raise ValidationError(f"Unknown column in join to '{rel.dim_table}'.")
    if fact_cols[rel.fact_col].dtype != dim_cols[rel.dim_col].dtype:
        raise ValidationError(f"Join columns for '{rel.dim_table}' have different types.")
    key = dim_cols[rel.dim_col]
    if key.null_pct > 0 or key.distinct != workspace.row_counts[rel.dim_table]:
        raise ValidationError(
            f"'{rel.dim_table}.{rel.dim_col}' is not unique, so joining it would duplicate rows."
        )


def build_select_sql(
    workspace: Workspace, fact_table: str, relationships: list[Relationship]
) -> tuple[str, list[str]]:
    """Return the SQL of the ``base`` view and the list of PII columns that were left out."""
    dropped: list[str] = []
    taken: set[str] = set()
    select_parts: list[str] = []
    for column in workspace.profiles[fact_table]:
        if column.is_pii:
            dropped.append(f"{fact_table}.{column.name}")
            continue
        taken.add(column.name)
        select_parts.append(f"f.{quote_identifier(column.name)}")
    joins: list[str] = []
    for index, rel in enumerate(relationships):
        check_relationship(workspace, fact_table, rel)
        alias = f"d{index}"
        joins.append(
            f"LEFT JOIN {quote_identifier(rel.dim_table)} {alias} "
            f"ON f.{quote_identifier(rel.fact_col)} = {alias}.{quote_identifier(rel.dim_col)}"
        )
        for column in workspace.profiles[rel.dim_table]:
            if column.is_pii:
                dropped.append(f"{rel.dim_table}.{column.name}")
            elif column.name != rel.dim_col:
                flat_name = unique_flat_name(column.name, rel.dim_table, taken)
                select_parts.append(
                    f"{alias}.{quote_identifier(column.name)} AS {quote_identifier(flat_name)}"
                )
    columns_sql = ', '.join(select_parts)
    sql = f"SELECT {columns_sql} FROM {quote_identifier(fact_table)} f " + " ".join(joins)
    return sql, dropped


def unique_flat_name(column: str, table: str, taken: set[str]) -> str:
    """Use the column name; on a collision prefix it with the table name."""
    name = column if column not in taken else f"{table}_{column}"
    counter = 2
    while name in taken:
        name = f"{table}_{column}_{counter}"
        counter += 1
    taken.add(name)
    return name


def compute_version(model_parts: dict) -> str:
    """Stable short hash of the dataset structure and column statistics."""
    text = json.dumps(model_parts, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def build_dataset(
    workspace: Workspace,
    fact_table: str,
    relationships: list[Relationship],
    roles: dict[str, str] | None = None,
) -> DatasetModel:
    """Create the ``base`` and ``data`` views and describe the result."""
    if fact_table not in workspace.profiles:
        raise ValidationError(f"Unknown fact table '{fact_table}'.")
    con = workspace.con
    select_sql, dropped = build_select_sql(workspace, fact_table, relationships)
    con.execute(f"CREATE OR REPLACE VIEW base AS {select_sql}")
    con.execute("CREATE OR REPLACE VIEW data AS SELECT * FROM base")
    columns = profile_view(con, "base")
    row_count = columns_row_count(con)
    if row_count != workspace.row_counts[fact_table]:
        raise ValidationError("The join changed the number of rows; check the selected keys.")
    final_roles = check_roles(roles, columns) if roles is not None else detect_roles(columns)
    for column in columns:
        column.role = next((r for r, name in final_roles.items() if name == column.name), None)
    parts = {
        "fact": fact_table,
        "tables": workspace.row_counts,
        "relationships": [r.model_dump() for r in relationships],
        "columns": [c.model_dump() for c in columns],
        "roles": final_roles,
    }
    return DatasetModel(
        dataset_id=uuid.uuid4().hex[:12],
        fact_table=fact_table,
        tables=dict(workspace.row_counts),
        relationships=relationships,
        columns=columns,
        roles=final_roles,
        row_count=row_count,
        dropped_pii=dropped,
        warnings=list(workspace.warnings),
        cleaning_report=list(workspace.cleaning_report),
        version=compute_version(parts),
    )


def columns_row_count(con: duckdb.DuckDBPyConnection) -> int:
    """Number of rows in ``base``."""
    return int(con.execute("SELECT COUNT(*) FROM base").fetchone()[0])


def check_roles(roles: dict[str, str], columns: list[ColumnInfo]) -> dict[str, str]:
    """Validate user-edited roles: every column must exist in the flat view."""
    by_name = {c.name: c for c in columns}
    bad_roles = [role for role in roles if role not in ROLE_SYNONYMS]
    if bad_roles:
        raise ValidationError(f"Unknown roles: {', '.join(bad_roles)}")
    unknown = [f"{role}={name}" for role, name in roles.items() if name not in by_name]
    if unknown:
        raise ValidationError(f"Unknown columns in role mapping: {', '.join(unknown)}")
    for role, name in roles.items():
        if not type_fits(role, by_name[name]):
            needs = "a date" if role == "date" else "numbers"
            raise ValidationError(
                f"Column '{name}' cannot be used as {role}: it must hold {needs}."
            )
    return dict(roles)
