"""Prompt content: no sensitive text, no golden-set leakage, fixed few-shot examples."""

from __future__ import annotations

import re

import pytest

from core.llm.egress import find_sensitive
from core.llm.providers import load_golden_questions, normalize_question
from core.metrics.registry import available_metrics
from core.query.prompts import FEW_SHOT, build_messages, build_user_payload, clean_text
from tests.fakes import make_settings


def test_few_shot_questions_are_not_golden_questions() -> None:
    shots = re.findall(r"^Question: (.+)$", FEW_SHOT, flags=re.MULTILINE)
    assert len(shots) == 6
    golden = {normalize_question(q.question) for q in load_golden_questions()}
    assert not {normalize_question(s) for s in shots} & golden


def test_few_shot_sql_is_not_a_golden_query() -> None:
    golden_sql = " ".join(q.expected_sql for q in load_golden_questions())
    assert "north_share_pct" not in golden_sql and "total_sales" not in golden_sql


@pytest.mark.parametrize("name", ["retail", "bank"])
@pytest.mark.parametrize("mode", ["demo", "cloud", "local"])
def test_prompt_contains_no_personal_data(name, mode, request) -> None:
    dataset = request.getfixturevalue(name)
    extra = {"llm_api_base": "http://localhost:11434"} if mode == "local" else {}
    settings = make_settings(privacy_mode=mode, send_category_values=True, **extra)
    payload = build_user_payload(
        dataset.model,
        available_metrics(dataset.model),
        "Which store had the highest revenue?",
        settings,
        ("2025-01-01", "2026-09-30"),
        "none",
    )
    messages = build_messages(payload)
    assert find_sensitive("\n".join(m["content"] for m in messages)) == []


def test_clean_text_is_single_line_and_short() -> None:
    assert clean_text("a\nb\t c") == "a b c"
    assert len(clean_text("z" * 500)) == 60
