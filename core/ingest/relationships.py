"""Propose fact-to-dimension joins by comparing column values in DuckDB.

A dimension key is a column whose values are unique. A fact column is proposed for a key when
at least 95% of its non-null values exist in the key column (containment). When several fact
columns fit, name similarity, then how much of the dimension the fact column covers, decide.
"""

from __future__ import annotations

from difflib import SequenceMatcher

import duckdb

from core.ingest.profiler import quote_identifier
from core.models import ColumnInfo, Relationship

MIN_CONTAINMENT = 0.95
NAME_SIMILARITY = 0.6
MIN_COVERAGE = 0.2  # a fact column must reach at least 20% of the dimension's keys
KEY_TYPES = ("integer", "text")


def name_similarity(left: str, right: str) -> float:
    """Similarity of two column names between 0 and 1."""
    return SequenceMatcher(None, left.lower(), right.lower()).ratio()


def unique_columns(columns: list[ColumnInfo], row_count: int) -> list[ColumnInfo]:
    """Return columns of a table that can act as a dimension key (no nulls, all distinct)."""
    return [
        c for c in columns
        if c.dtype in KEY_TYPES and c.null_pct == 0 and c.distinct == row_count and row_count > 0
    ]


def overlap(
    con: duckdb.DuckDBPyConnection, fact: str, fact_col: str, dim: str, dim_col: str
) -> tuple[float, float]:
    """Return (containment, coverage) of a fact column in a dimension key column."""
    f, d = quote_identifier(fact_col), quote_identifier(dim_col)
    row = con.execute(
        f"""
        WITH fact_values AS (SELECT {f} AS v, COUNT(*) AS n FROM {quote_identifier(fact)}
                             WHERE {f} IS NOT NULL GROUP BY {f})
        SELECT COALESCE(SUM(n) FILTER (WHERE k.{d} IS NOT NULL), 0)::DOUBLE / NULLIF(SUM(n), 0),
               COUNT(k.{d})::DOUBLE / NULLIF((SELECT COUNT(*) FROM {quote_identifier(dim)}), 0)
        FROM fact_values LEFT JOIN {quote_identifier(dim)} k
             ON CAST(v AS VARCHAR) = CAST(k.{d} AS VARCHAR)
        """
    ).fetchone()
    return float(row[0] or 0.0), float(row[1] or 0.0)


def score_candidate(fact_col: str, dim_col: str, containment: float, coverage: float) -> float:
    """Higher is better. Similar names beat coincidental value overlap."""
    similarity = name_similarity(fact_col, dim_col)
    boost = 1.0 if similarity >= NAME_SIMILARITY else 0.0
    return containment + boost + 0.5 * coverage + 0.1 * similarity


def best_for_dimension(
    con: duckdb.DuckDBPyConnection,
    fact: str,
    fact_columns: list[ColumnInfo],
    dim: str,
    dim_keys: list[ColumnInfo],
    fact_rows: int,
) -> Relationship | None:
    """Find the best (fact column, dimension key) pair for one dimension table."""
    best: tuple[float, Relationship] | None = None
    for key in dim_keys:
        for column in fact_columns:
            if column.dtype != key.dtype or column.is_pii:
                continue
            containment, coverage = overlap(con, fact, column.name, dim, key.name)
            if containment < MIN_CONTAINMENT or coverage < MIN_COVERAGE:
                continue
            score = score_candidate(column.name, key.name, containment, coverage)
            unique_fact = column.distinct == fact_rows and column.null_pct == 0
            relationship = Relationship(
                fact_col=column.name,
                dim_table=dim,
                dim_col=key.name,
                containment=round(containment, 4),
                cardinality="one_to_one" if unique_fact else "many_to_one",
            )
            if best is None or score > best[0]:
                best = (score, relationship)
    return best[1] if best else None


def propose_relationships(
    con: duckdb.DuckDBPyConnection,
    fact_table: str,
    profiles: dict[str, list[ColumnInfo]],
    row_counts: dict[str, int],
) -> list[Relationship]:
    """Propose at most one join per dimension table. The UI asks the user to confirm."""
    proposals: list[Relationship] = []
    for dim, dim_columns in profiles.items():
        if dim == fact_table:
            continue
        keys = unique_columns(dim_columns, row_counts[dim])
        found = best_for_dimension(
            con, fact_table, profiles[fact_table], dim, keys, row_counts[fact_table]
        )
        if found:
            proposals.append(found)
    return proposals
