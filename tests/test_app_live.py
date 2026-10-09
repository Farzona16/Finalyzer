"""The app with a (fake) live model: verified answers, clarification, audit and safe fallbacks."""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from app.state import CHOICE_KEY
from core.config import get_settings
from core.llm import gateway as gateway_module
from tests.fakes import FakeProvider, clarify_reply, query_reply
from tests.test_app_smoke import APP_FILE, TIMEOUT, assert_clean, texts

COUNT = "SELECT COUNT(*) AS n FROM data"


def model_reply(messages: list[dict[str, str]], temperature: float) -> str:
    """Fake model: SQL for the count question, a clarification for vague ones, junk otherwise."""
    system = messages[0]["content"]
    if "translate business questions" not in system:
        return "this is not json"
    question = messages[-1]["content"].splitlines()[-1].lower()  # "Question: ..." is last
    if "best" in question:
        return clarify_reply("Best by which measure?")
    return query_reply(COUNT)


@pytest.fixture
def live_env(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    """Settings with a key and model; the provider behind the gateway is a scripted fake."""
    for name, value in {
        "LLM_MODE": "live", "ANTHROPIC_API_KEY": "test-key", "CLAUDE_MODEL_MAIN": "fake-model",
        "APP_PASSWORD": "", "PRIVACY_MODE": "demo", "LLM_MAX_RETRIES": "0",
        "SQL_CANDIDATES": "3", "ASK_CANDIDATES": "3", "CACHE_IDENTICAL_PROMPTS": "false",
    }.items():
        monkeypatch.setenv(name, value)
    provider = FakeProvider([model_reply])
    monkeypatch.setattr(gateway_module, "build_providers", lambda settings, runtime: [provider])
    get_settings.cache_clear()
    yield provider
    get_settings.cache_clear()


def open_retail() -> AppTest:
    """Start the app on the Retail demo."""
    at = AppTest.from_file(APP_FILE, default_timeout=TIMEOUT).run()
    at.sidebar.radio(key=CHOICE_KEY).set_value("retail").run()
    assert_clean(at)
    return at


def ask(at: AppTest, question: str) -> None:
    """Type a question and press Ask."""
    at.text_input(key="fz_question").set_value(question)
    next(b for b in at.button if b.label == "Ask").click().run()


def test_live_answer_is_verified_and_audited(live_env: FakeProvider) -> None:
    at = open_retail()
    assert "AI: fake-model" in texts(at.sidebar.caption)
    ask(at, "How many sales are there in total?")
    assert_clean(at)
    assert "Verified" in texts(at.markdown) and "Unverified" not in texts(at.markdown)
    assert "SELECT COUNT" in "\n".join(c.value for c in at.code)
    assert len(live_env.calls) == 3  # three candidate queries
    counters = {m.label: m.value for m in at.metric}
    assert int(counters["Bytes sent outside"].replace(",", "")) > 0
    assert counters["Blocked calls"] == "0"


def test_clarification_is_shown_with_metric_buttons(live_env: FakeProvider) -> None:
    at = open_retail()
    ask(at, "Which store is the best?")
    assert_clean(at)
    assert "Best by which measure?" in texts(at.info)
    buttons = [b for b in at.button if b.key and b.key.startswith("fz_clarify:")]
    assert buttons
    buttons[0].click().run()
    assert_clean(at)
    assert "Use the metric" in texts(at.markdown)


def test_unusable_ai_wording_falls_back_to_templates(live_env: FakeProvider) -> None:
    at = open_retail()
    at.button(key="fz_run_insights").click().run()
    assert_clean(at)
    assert "Template-written" in texts(at.markdown)
    assert "Generated without AI" in texts(at.caption)
    assert "Revenue" in texts(at.markdown)


def test_payload_with_personal_data_is_blocked_and_shown_in_the_audit(
    live_env: FakeProvider,
) -> None:
    at = open_retail()
    ask(at, "How many sales for anna.smith@example.com?")
    assert not at.exception
    counters = {m.label: m.value for m in at.metric}
    assert counters["Blocked calls"] != "0"
    assert live_env.calls == []
