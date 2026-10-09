"""Offline mode: only the golden questions are answered, and nothing leaves the machine."""

from __future__ import annotations

import pytest

from core.config import Settings
from core.llm.audit import AuditLog
from core.llm.gateway import LLMGateway
from core.llm.providers import OFFLINE_MESSAGE, load_golden_questions
from core.metrics.registry import available_metrics
from core.query.executor import result_hash
from core.query.nl2sql import NL2SQLAgent
from tests.fakes import make_settings

GOLDEN = load_golden_questions()


def offline_agent(dataset) -> tuple[NL2SQLAgent, AuditLog]:
    settings = make_settings(llm_mode="offline")
    audit = AuditLog()
    gateway = LLMGateway(settings, audit)
    metrics = available_metrics(dataset.model)
    return NL2SQLAgent(dataset.con, dataset.model, gateway, settings, metrics, dataset.name), audit


def test_offline_is_chosen_without_a_key() -> None:
    settings = Settings(_env_file=None, llm_model="some-model", llm_api_key="")
    assert settings.effective_llm_mode == "offline"
    assert LLMGateway(settings).is_offline


def test_live_is_chosen_with_a_key() -> None:
    gateway = LLMGateway(make_settings(anthropic_api_key="k", claude_model_main="claude-test"))
    assert not gateway.is_offline and gateway.provider_names() == ["anthropic"]


@pytest.mark.parametrize("question", GOLDEN, ids=lambda q: q.id)
def test_every_golden_question_is_answered(question, request) -> None:
    dataset = request.getfixturevalue(question.dataset)
    agent, audit = offline_agent(dataset)
    answer = agent.ask(question.question)
    expected = dataset.con.execute(question.expected_sql).fetchall()
    assert answer.status == "ok"
    assert answer.result.verified
    assert answer.text
    assert result_hash(answer.result.rows) == result_hash([list(r) for r in expected])
    assert audit.events == []


def test_question_matching_ignores_case_and_punctuation(retail) -> None:
    agent, _ = offline_agent(retail)
    original = next(q for q in GOLDEN if q.dataset == "retail")
    shouted = "  " + original.question.upper().rstrip("?") + " ??  "
    assert agent.ask(shouted).status == "ok"


def test_unknown_question_gets_the_offline_message(retail) -> None:
    agent, audit = offline_agent(retail)
    answer = agent.ask("What is the airspeed velocity of an unladen swallow?")
    assert answer.status == "offline"
    assert answer.text == OFFLINE_MESSAGE
    assert audit.events == []


def test_golden_questions_are_not_answered_on_the_other_dataset(bank) -> None:
    agent, _ = offline_agent(bank)
    retail_question = next(q for q in GOLDEN if q.dataset == "retail")
    assert agent.ask(retail_question.question).status == "offline"


def test_suggestions_come_from_the_dataset() -> None:
    from core.llm.providers import OfflineProvider

    suggestions = OfflineProvider().suggestions("bank", 6)
    bank_questions = {q.question for q in GOLDEN if q.dataset == "bank"}
    assert len(suggestions) == 6 and set(suggestions) <= bank_questions
