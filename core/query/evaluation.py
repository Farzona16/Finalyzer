"""Measure NL to SQL accuracy on the golden questions.

A question passes when the answer's result set equals the result of the stored SQL. Row order
and column names are ignored and every number is rounded to two decimals.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any

import duckdb

from core.config import Settings
from core.errors import UserFacingError
from core.llm.audit import AuditLog
from core.llm.gateway import LLMGateway
from core.llm.providers import GoldenQuestion
from core.metrics.registry import available_metrics
from core.models import DatasetModel, QueryResult
from core.query.executor import execute_query
from core.query.nl2sql import NL2SQLAgent

DECIMALS = 2


@dataclass
class QuestionOutcome:
    """The result of running one golden question."""

    id: str
    dataset: str
    question: str
    passed: bool
    status: str
    verified: bool
    latency_s: float
    tokens: int
    note: str = ""


def normalize_value(value: Any) -> Any:
    """Numbers rounded to two decimals (ints and floats compare equal); the rest as text."""
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int | float):
        return round(float(value), DECIMALS) + 0.0  # + 0.0 turns -0.0 into 0.0
    return str(value)


def normalized_rows(result: QueryResult) -> list[tuple]:
    """Rows in a canonical order with normalized values."""
    rows = [tuple(normalize_value(v) for v in row) for row in result.rows]
    return sorted(rows, key=repr)


def same_result(actual: QueryResult, expected: QueryResult) -> bool:
    """True when both results hold the same rows (any order) and have the same width."""
    if len(actual.columns) != len(expected.columns):
        return False
    return normalized_rows(actual) == normalized_rows(expected)


def judge(answer_result: QueryResult | None, expected: QueryResult) -> tuple[bool, str]:
    """Pass or fail with a short reason."""
    if answer_result is None:
        return False, "no query result"
    if same_result(answer_result, expected):
        return True, ""
    return False, (
        f"different result: {answer_result.row_count} rows x {len(answer_result.columns)} cols, "
        f"expected {expected.row_count} x {len(expected.columns)}"
    )


def run_question(
    agent: NL2SQLAgent,
    con: duckdb.DuckDBPyConnection,
    golden: GoldenQuestion,
    audit: AuditLog,
    settings: Settings,
) -> QuestionOutcome:
    """Ask one question through the real pipeline and compare with the stored SQL."""
    expected = execute_query(con, golden.expected_sql, settings)
    tokens_before = audit.tokens_used
    started = time.perf_counter()
    try:
        answer = agent.ask(golden.question)
        status, result, note = answer.status, answer.result, answer.text
    except UserFacingError as exc:
        status, result, note = "failed", None, exc.message
    latency = time.perf_counter() - started
    passed, reason = judge(result if status == "ok" else None, expected)
    return QuestionOutcome(
        id=golden.id, dataset=golden.dataset, question=golden.question, passed=passed,
        status=status, verified=bool(result and result.verified and status == "ok"),
        latency_s=round(latency, 3), tokens=audit.tokens_used - tokens_before,
        note="" if passed else (reason if status == "ok" else note[:200]),
    )


def evaluate_dataset(
    con: duckdb.DuckDBPyConnection,
    model: DatasetModel,
    dataset: str,
    questions: list[GoldenQuestion],
    gateway: LLMGateway,
    settings: Settings,
) -> list[QuestionOutcome]:
    """Run every golden question of one demo dataset."""
    metrics = available_metrics(model)
    agent = NL2SQLAgent(con, model, gateway, settings, metrics, dataset)
    return [
        run_question(agent, con, q, gateway.audit, settings)
        for q in questions if q.dataset == dataset
    ]


def summarize(outcomes: list[QuestionOutcome], audit: AuditLog) -> dict[str, Any]:
    """Overall accuracy, verified vs unverified, mean latency and token totals."""
    total = len(outcomes)
    passed = sum(o.passed for o in outcomes)
    verified = [o for o in outcomes if o.verified]
    unverified = [o for o in outcomes if not o.verified]
    events = [e for e in audit.events if not e.blocked]
    return {
        "total": total,
        "passed": passed,
        "accuracy": round(passed / total, 4) if total else 0.0,
        "verified": {"count": len(verified), "passed": sum(o.passed for o in verified)},
        "unverified": {"count": len(unverified), "passed": sum(o.passed for o in unverified)},
        "mean_latency_s": round(sum(o.latency_s for o in outcomes) / total, 3) if total else 0.0,
        "tokens_in": sum(e.tokens_in for e in events),
        "tokens_out": sum(e.tokens_out for e in events),
        "model_calls": len(events),
    }


def outcome_rows(outcomes: list[QuestionOutcome]) -> list[dict[str, Any]]:
    """Outcomes as plain dictionaries for the JSON report."""
    return [asdict(o) for o in outcomes]
