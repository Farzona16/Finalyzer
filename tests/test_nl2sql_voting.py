"""Natural-language questions: candidate generation, voting, repair and refusal paths."""

from __future__ import annotations

import json
import re

import pytest

from core.errors import LLMUnavailableError
from core.llm.audit import AuditLog
from core.llm.gateway import LLMGateway
from core.metrics.registry import available_metrics
from core.query.nl2sql import NL2SQLAgent
from tests.fakes import (
    FakeProvider,
    clarify_reply,
    make_settings,
    query_reply,
    refuse_reply,
)

COUNT = "SELECT COUNT(*) AS n FROM data"
SUM = "SELECT SUM(amount) AS total FROM data"
AVG = "SELECT AVG(amount) AS mean FROM data"
QUESTION = "How many sales do we have?"


def make_agent(retail, replies, **settings) -> tuple[NL2SQLAgent, FakeProvider, AuditLog]:
    """An agent on the retail demo data whose model is a scripted fake."""
    settings.setdefault("sql_candidates", 3)
    config = make_settings(**settings)
    provider = FakeProvider(replies)
    audit = AuditLog()
    gateway = LLMGateway(config, audit, provider)
    metrics = available_metrics(retail.model)
    agent = NL2SQLAgent(retail.con, retail.model, gateway, config, metrics, "retail")
    return agent, provider, audit


def test_three_agreeing_candidates_are_verified(retail) -> None:
    agent, provider, _ = make_agent(retail, [query_reply(COUNT)])
    answer = agent.ask(QUESTION)
    assert answer.status == "ok"
    assert answer.result.verified
    assert (answer.result.candidates_total, answer.result.candidates_agreeing) == (3, 3)
    assert answer.result.rows == [[retail.model.row_count]]
    assert answer.chart.kind == "kpi"
    assert len(provider.calls) == 3
    assert sorted(provider.temperatures) == [0.0, 0.5, 0.5]
    assert answer.result.warnings == []


def test_candidates_with_different_sql_but_same_result_agree(retail) -> None:
    same = "SELECT COUNT(sale_id) AS how_many FROM data"
    agent, _, _ = make_agent(retail, [query_reply(COUNT), query_reply(same), query_reply(COUNT)])
    answer = agent.ask(QUESTION)
    assert answer.result.verified and answer.result.candidates_agreeing == 3


def test_two_against_one_is_verified_with_a_note(retail) -> None:
    agent, _, _ = make_agent(retail, [query_reply(COUNT), query_reply(SUM), query_reply(COUNT)])
    answer = agent.ask(QUESTION)
    assert answer.status == "ok"
    assert answer.result.verified
    assert (answer.result.candidates_total, answer.result.candidates_agreeing) == (3, 2)
    assert answer.result.rows == [[retail.model.row_count]]
    assert any("2 of 3" in w for w in answer.result.warnings)


def test_all_different_is_unverified(retail) -> None:
    agent, _, _ = make_agent(retail, [query_reply(COUNT), query_reply(SUM), query_reply(AVG)])
    answer = agent.ask(QUESTION)
    assert answer.status == "ok"
    assert not answer.result.verified
    assert answer.result.candidates_agreeing == 1
    assert any("Unverified" in w for w in answer.result.warnings)
    assert answer.result.rows == [[retail.model.row_count]]  # first candidate wins ties


def test_identical_live_prompts_are_sent_once(retail) -> None:
    from core.llm.gateway import clear_prompt_cache

    clear_prompt_cache()
    agent, provider, _ = make_agent(
        retail, [query_reply(COUNT)], sql_candidates=3, cache_identical_prompts=True
    )
    agent.ask(QUESTION)
    assert len(provider.calls) == 1
    clear_prompt_cache()


def test_single_candidate_setting_is_unverified(retail) -> None:
    agent, provider, _ = make_agent(retail, [query_reply(COUNT)], sql_candidates=1)
    answer = agent.ask(QUESTION)
    assert not answer.result.verified
    assert any("one candidate" in w.lower() for w in answer.result.warnings)
    assert len(provider.calls) == 1


def test_failed_candidate_counts_against_agreement(retail) -> None:
    def reply(messages: list[dict[str, str]], temperature: float) -> str:
        if temperature == 0.0:
            return query_reply(COUNT)
        return query_reply("SELECT nope FROM data")  # the other two never produce a result

    agent, _, _ = make_agent(retail, [reply])
    answer = agent.ask(QUESTION)
    assert answer.status == "ok"
    assert not answer.result.verified
    assert (answer.result.candidates_total, answer.result.candidates_agreeing) == (3, 1)


def repairing(bad: str, good: str):
    """A reply script: bad SQL first, corrected SQL once the error message was sent back."""

    def reply(messages: list[dict[str, str]], temperature: float) -> str:
        repaired = "failed with this error" in messages[-1]["content"]
        return query_reply(good if repaired else bad)

    return reply


def test_repair_uses_the_exact_error(retail) -> None:
    agent, provider, _ = make_agent(retail, [repairing("SELECT no_such_col FROM data", COUNT)])
    answer = agent.ask(QUESTION)
    assert answer.status == "ok" and answer.result.verified
    assert len(provider.calls) == 6  # three candidates, one repair each
    repair_turns = [c[-1]["content"] for c in provider.calls if len(c) == 4]
    assert len(repair_turns) == 3
    assert all("no_such_col" in turn for turn in repair_turns)


def test_at_most_two_repairs_per_candidate(retail) -> None:
    agent, provider, _ = make_agent(retail, [query_reply("SELECT nope FROM data")])
    answer = agent.ask(QUESTION)
    assert answer.status == "failed"
    assert "could not answer" in answer.text
    assert len(provider.calls) == 9  # three candidates x (1 try + 2 repairs)


def test_dangerous_sql_from_the_model_is_never_run(retail) -> None:
    agent, _, _ = make_agent(retail, [query_reply("DROP TABLE base")])
    answer = agent.ask("Please clean up the old data")
    assert answer.status == "failed"
    assert retail.con.execute("SELECT COUNT(*) FROM base").fetchone()[0] > 0


def test_clarify_and_refuse_use_a_single_call(retail) -> None:
    agent, provider, _ = make_agent(retail, [clarify_reply("Which month do you mean?")])
    answer = agent.ask("How did we do?")
    assert answer.status == "clarify" and answer.text == "Which month do you mean?"
    assert answer.result is None and len(provider.calls) == 1

    agent, provider, _ = make_agent(retail, [refuse_reply("I can only read data.")])
    answer = agent.ask("Delete every order")
    assert answer.status == "refuse" and len(provider.calls) == 1


def test_invalid_json_is_repaired_once(retail) -> None:
    def reply(messages: list[dict[str, str]], temperature: float) -> str:
        return query_reply(COUNT) if "could not be used" in messages[-1]["content"] else "no json"

    agent, provider, _ = make_agent(retail, [reply])
    answer = agent.ask(QUESTION)
    assert answer.status == "ok" and answer.result.verified
    assert len(provider.calls) == 6


def test_json_in_a_code_fence_is_accepted(retail) -> None:
    fenced = "Here you go:\n```json\n" + query_reply(COUNT) + "\n```"
    agent, _, _ = make_agent(retail, [fenced], sql_candidates=1)
    assert agent.ask(QUESTION).status == "ok"


def test_unusable_replies_give_a_friendly_failure(retail) -> None:
    agent, _, _ = make_agent(retail, ["nonsense"], sql_candidates=1)
    answer = agent.ask(QUESTION)
    assert answer.status == "failed"
    assert "format" in answer.text


def test_model_outage_is_reported(retail) -> None:
    agent, _, _ = make_agent(retail, [LLMUnavailableError("The AI service is busy.")])
    answer = agent.ask(QUESTION)
    assert answer.status == "failed" and "busy" in answer.text


def test_personal_data_in_a_question_is_blocked(retail) -> None:
    agent, provider, audit = make_agent(retail, [query_reply(COUNT)])
    answer = agent.ask("What did anna.lee@example.com buy?")
    assert answer.status == "failed" and "personal data" in answer.text
    assert provider.calls == [] and audit.blocked_count >= 1


def test_only_known_metric_ids_are_kept(retail) -> None:
    reply = query_reply(SUM, metric_ids=["revenue", "made_up"])
    agent, _, _ = make_agent(retail, [reply])
    answer = agent.ask("What is our revenue?")
    assert answer.result.metric_ids == ["revenue"]


def test_empty_and_long_questions(retail) -> None:
    agent, provider, _ = make_agent(retail, [query_reply(COUNT)])
    assert agent.ask("   ").status == "clarify"
    assert agent.ask("x" * 600).status == "failed"
    assert provider.calls == []


def test_prompt_shows_schema_categories_metrics_and_dates(retail) -> None:
    agent, provider, _ = make_agent(retail, [query_reply(COUNT)], sql_candidates=1)
    agent.ask(QUESTION)
    payload = "\n".join(m["content"] for m in provider.calls[0])
    assert "Table `tbl_a` columns" in payload
    assert "Electronics" not in payload
    assert "values omitted" in payload
    assert "revenue" in payload and "SUM(" in payload
    assert "Dates in `tbl_a` right now:" in payload
    assert json.loads(json.dumps(provider.calls[0]))[0]["role"] == "system"
    for name in ("amount", "sale_date", "store_name", "city"):
        assert not re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", payload)


def test_cloud_mode_hides_category_values_by_default(retail) -> None:
    agent, provider, _ = make_agent(
        retail, [query_reply(COUNT)], sql_candidates=1, privacy_mode="cloud"
    )
    agent.ask(QUESTION)
    payload = "\n".join(m["content"] for m in provider.calls[0])
    assert "Electronics" not in payload
    assert "values omitted" in payload


@pytest.mark.parametrize("mode", ["demo", "cloud"])
def test_query_results_are_not_sent_to_the_model(retail, mode) -> None:
    agent, provider, _ = make_agent(retail, [query_reply(SUM)], privacy_mode=mode)
    answer = agent.ask("What is the total amount?")
    total = answer.result.rows[0][0]
    sent = json.dumps(provider.calls)
    assert f"{total:.2f}" not in sent and str(round(total)) not in sent
