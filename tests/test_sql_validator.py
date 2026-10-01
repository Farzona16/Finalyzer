"""SQL guardrails: malicious statements are rejected, valid analytics queries are accepted."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from core.errors import ValidationError
from core.llm.providers import load_golden_questions
from core.query.executor import execute_query, result_hash
from core.query.sql_validator import MAX_SQL_LENGTH, validate_and_explain, validate_sql
from tests.fakes import make_settings

DATA = Path("tests/data")


def read_lines(name: str) -> list[str]:
    """Non-empty lines of a corpus file."""
    text = (DATA / name).read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line.strip()]


MALICIOUS = read_lines("malicious_sql.txt")
VALID = read_lines("valid_sql.txt")
GOLDEN = load_golden_questions()


def test_corpus_sizes() -> None:
    assert len(MALICIOUS) >= 40
    assert len(VALID) >= 25


@pytest.mark.parametrize("sql", MALICIOUS)
def test_malicious_sql_is_rejected(sql: str, retail) -> None:
    with pytest.raises(ValidationError):
        execute_query(retail.con, sql, make_settings())


@pytest.mark.parametrize("sql", MALICIOUS)
def test_static_validator_alone_rejects_malicious_sql(sql: str) -> None:
    """Layer 1 (parsing rules) must not depend on DuckDB's own protections."""
    with pytest.raises(ValidationError):
        validate_sql(sql)


def test_data_survives_the_malicious_corpus(retail) -> None:
    for sql in MALICIOUS:
        with pytest.raises(ValidationError):
            validate_and_explain(retail.con, sql)
    count = retail.con.execute("SELECT COUNT(*) FROM data").fetchone()[0]
    assert count == retail.model.row_count
    assert retail.con.execute("SELECT COUNT(*) FROM base").fetchone()[0] == count


@pytest.mark.parametrize("sql", VALID)
def test_valid_sql_is_accepted(sql: str, retail) -> None:
    result = execute_query(retail.con, sql, make_settings())
    assert result.columns
    assert result.row_count >= 0


def test_long_sql_is_rejected() -> None:
    sql = "SELECT " + " + ".join(["1"] * 3000)
    assert len(sql) > MAX_SQL_LENGTH
    with pytest.raises(ValidationError, match="longer"):
        validate_sql(sql)


@pytest.mark.parametrize("sql", ["", "   ", "\n"])
def test_empty_sql_is_rejected(sql: str) -> None:
    with pytest.raises(ValidationError):
        validate_sql(sql)


def test_unknown_column_is_caught_by_explain(retail) -> None:
    with pytest.raises(ValidationError):
        validate_and_explain(retail.con, "SELECT no_such_column FROM data")


def test_normalized_sql_has_no_comments() -> None:
    normalized = validate_sql("SELECT 1 AS x /* hidden */ FROM data -- trailing")
    assert "hidden" not in normalized and "trailing" not in normalized


def test_connection_configuration_is_locked(retail) -> None:
    with pytest.raises(duckdb.Error):
        retail.con.execute("SET enable_external_access = true")
    with pytest.raises(duckdb.Error):
        retail.con.execute("COPY (SELECT 1) TO 'should_not_exist.csv'")
    assert not Path("should_not_exist.csv").exists()


@pytest.mark.parametrize("question", GOLDEN, ids=lambda q: q.id)
def test_normalization_keeps_golden_results(question, request) -> None:
    """The text we execute (normalized) returns the same rows as the original golden SQL."""
    dataset = request.getfixturevalue(question.dataset)
    original = dataset.con.execute(question.expected_sql).fetchall()
    result = execute_query(dataset.con, question.expected_sql, make_settings())
    original_rows = [[v for v in row] for row in original]
    assert result.row_count == len(original_rows)
    assert result_hash(result.rows) == result_hash(original_rows)
