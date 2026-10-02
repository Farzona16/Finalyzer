"""The evaluation harness must pass right answers, fail wrong ones and count honestly."""

from __future__ import annotations

import pytest

from core.llm.audit import AuditLog
from core.llm.gateway import LLMGateway
from core.llm.providers import GoldenQuestion, load_golden_questions
from core.models import QueryResult
from core.query.evaluation import (
    evaluate_dataset,
    normalize_value,
    same_result,
    summarize,
)
from scripts.eval_golden import safe_name
from tests.fakes import FakeProvider, make_settings, query_reply
from tests.helpers import DemoDataset


def result(columns: list[str], rows: list[list]) -> QueryResult:
    """A QueryResult with the given content."""
    return QueryResult(sql="SELECT 1", columns=columns, rows=rows, row_count=len(rows))


def test_numbers_are_rounded_and_ints_equal_floats() -> None:
    assert normalize_value(3) == normalize_value(3.0)
    assert normalize_value(1.0049) == normalize_value(1.0)
    assert normalize_value(1.006) != normalize_value(1.0)
    assert normalize_value(-0.0) == normalize_value(0)
    assert normalize_value(None) is None
    assert normalize_value(True) == "True"


def test_row_order_and_column_names_do_not_matter() -> None:
    a = result(["x", "n"], [["a", 1], ["b", 2.004]])
    b = result(["label", "total"], [["b", 2.0], ["a", 1.0]])
    assert same_result(a, b)


@pytest.mark.parametrize(
    "other",
    [
        result(["x", "n"], [["a", 1]]),
        result(["x", "n"], [["a", 1], ["b", 3]]),
        result(["x"], [["a"], ["b"]]),
        result(["x", "n"], [["a", 1], ["b", 2], ["c", 3]]),
    ],
)
def test_different_results_are_not_equal(other: QueryResult) -> None:
    reference = result(["x", "n"], [["a", 1], ["b", 2]])
    assert not same_result(other, reference)


def golden(dataset: str, count: int = 1) -> list[GoldenQuestion]:
    """The first golden questions of a dataset."""
    return [q for q in load_golden_questions() if q.dataset == dataset][:count]


def test_offline_harness_passes_all_forty_questions(retail: DemoDataset, bank: DemoDataset) -> None:
    settings = make_settings(llm_mode="offline")
    audit = AuditLog()
    gateway = LLMGateway(settings, audit)
    outcomes = []
    for name, demo in (("retail", retail), ("bank", bank)):
        outcomes += evaluate_dataset(
            demo.con, demo.model, name, load_golden_questions(), gateway, settings
        )
    assert len(outcomes) == 40
    failed = [o.id for o in outcomes if not o.passed]
    assert failed == []
    summary = summarize(outcomes, audit)
    assert summary["accuracy"] == 1.0 and summary["model_calls"] == 0


def test_wrong_model_answer_fails_and_right_answer_passes(retail: DemoDataset) -> None:
    question = golden("retail")[0]
    wrong = FakeProvider([query_reply("SELECT COUNT(*) AS n FROM data WHERE 1 = 0")])
    right = FakeProvider([query_reply(question.expected_sql)])
    outcomes = {}
    for label, provider in (("wrong", wrong), ("right", right)):
        settings = make_settings()
        gateway = LLMGateway(settings, AuditLog(), provider)
        outcomes[label] = evaluate_dataset(
            retail.con, retail.model, "retail", [question], gateway, settings
        )[0]
    assert not outcomes["wrong"].passed and "different result" in outcomes["wrong"].note
    assert outcomes["right"].passed and outcomes["right"].verified
    assert outcomes["right"].tokens > 0


def test_unverified_answers_are_counted_separately(retail: DemoDataset) -> None:
    question = golden("retail")[0]
    replies = [
        query_reply(question.expected_sql),
        query_reply("SELECT COUNT(*) AS n FROM data"),
        query_reply("SELECT SUM(amount) AS n FROM data"),
    ]
    settings = make_settings(sql_candidates=3)
    gateway = LLMGateway(settings, AuditLog(), FakeProvider(replies))
    outcomes = evaluate_dataset(retail.con, retail.model, "retail", [question], gateway, settings)
    summary = summarize(outcomes, gateway.audit)
    assert summary["total"] == 1
    assert summary["verified"]["count"] + summary["unverified"]["count"] == 1
    assert summary["model_calls"] == 3


def test_failed_pipeline_is_a_failure_not_a_crash(retail: DemoDataset) -> None:
    question = golden("retail")[0]
    settings = make_settings()
    gateway = LLMGateway(settings, AuditLog(), FakeProvider(["not json at all"]))
    outcome = evaluate_dataset(retail.con, retail.model, "retail", [question], gateway, settings)[0]
    assert not outcome.passed and outcome.status == "failed"


def test_report_file_name_is_safe() -> None:
    assert safe_name("openai/gpt-4o-mini") == "openai-gpt-4o-mini"
    assert safe_name("ollama/llama3:8b") == "ollama-llama3-8b"
    assert safe_name("///") == "model"
