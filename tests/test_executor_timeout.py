"""Runaway queries are stopped within the time limit and leave the connection usable."""

from __future__ import annotations

import time

import pytest

from core.errors import QueryTimeoutError
from core.query.executor import execute_query, format_for_hash, result_hash
from tests.fakes import make_settings

TIMEOUT_S = 2
HEAVY_CROSS_JOIN = "SELECT COUNT(*) AS n FROM data a, data b, data c WHERE a.amount < b.amount"
RECURSIVE_BOMB = (
    "WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM t) SELECT COUNT(*) AS n FROM t"
)


@pytest.mark.parametrize("sql", [HEAVY_CROSS_JOIN, RECURSIVE_BOMB], ids=["cross_join", "cte_bomb"])
def test_runaway_query_is_stopped(sql: str, retail) -> None:
    settings = make_settings(query_timeout_s=TIMEOUT_S)
    started = time.perf_counter()
    with pytest.raises(QueryTimeoutError):
        execute_query(retail.con, sql, settings)
    assert time.perf_counter() - started < TIMEOUT_S + 2
    follow_up = execute_query(retail.con, "SELECT COUNT(*) AS n FROM data", settings)
    assert follow_up.rows[0][0] == retail.model.row_count


def test_row_limit_is_applied(retail) -> None:
    settings = make_settings(result_row_limit=100)
    result = execute_query(retail.con, "SELECT sale_id FROM data", settings)
    assert result.row_count == 100
    assert any("first 100" in w for w in result.warnings)


def test_result_values_are_plain_python(retail) -> None:
    result = execute_query(
        retail.con, "SELECT MIN(sale_date) AS d, SUM(amount) AS s FROM data", make_settings()
    )
    assert isinstance(result.rows[0][0], str) and len(result.rows[0][0]) == 10
    assert isinstance(result.rows[0][1], float)


def test_hash_ignores_row_order_and_float_noise() -> None:
    first = [["a", 1.00001], ["b", 2]]
    second = [["b", 2.0], ["a", 1.00002]]
    assert result_hash(first) == result_hash(second)
    assert result_hash(first) != result_hash([["a", 1.5], ["b", 2]])
    assert format_for_hash(3) == format_for_hash(3.0)
