"""End-to-end smoke test of the Streamlit app on the offline Demo: Retail path."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app import ui_audit
from app.state import CHOICE_KEY
from core.config import get_settings

APP_FILE = Path(__file__).resolve().parents[1] / "app" / "main.py"
TIMEOUT = 180


@pytest.fixture
def offline_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No API key, no password, demo mode: exactly what a first-time visitor gets."""
    for name, value in {
        "LLM_MODE": "offline", "LLM_API_KEY": "", "APP_PASSWORD": "", "PRIVACY_MODE": "demo",
        "LLM_MODEL": "",
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def texts(elements) -> str:
    """All text of a list of Streamlit test elements."""
    return "\n".join(str(getattr(e, "value", "")) for e in elements)


def assert_clean(at: AppTest) -> None:
    """No uncaught exception and no error box on the page."""
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]


def start_retail() -> AppTest:
    """Open the app and pick the Retail demo."""
    at = AppTest.from_file(APP_FILE, default_timeout=TIMEOUT).run()
    assert_clean(at)
    at.sidebar.radio(key=CHOICE_KEY).set_value("retail").run()
    assert_clean(at)
    return at


def test_first_page_needs_no_key_and_shows_guidance(offline_env: None) -> None:
    at = AppTest.from_file(APP_FILE, default_timeout=TIMEOUT).run()
    assert_clean(at)
    assert [t.label for t in at.tabs] == ["Data", "Dashboard", "Insights", "Ask", "Audit"]
    assert "Do not upload real personal data" in texts(at.warning)
    assert "AI: offline" in texts(at.sidebar.caption)


def test_demo_retail_end_to_end_offline(offline_env: None) -> None:
    at = start_retail()
    assert "fz-card" in texts(at.markdown)  # KPI cards are drawn
    assert any(s.label == "Second metric" for s in at.selectbox)

    at.button(key="fz_run_insights").click().run()
    assert_clean(at)
    assert "Template-written" in texts(at.markdown)
    assert "Revenue" in texts(at.markdown)

    at.button(key="fz_suggest:0").click().run()
    assert_clean(at)
    assert "Verified" in texts(at.markdown)
    assert "Unverified" not in texts(at.markdown)

    counters = {m.label: m.value for m in at.metric}
    assert counters["Bytes sent outside"] == "0"
    assert counters["Blocked calls"] == "0"


def test_switching_to_bank_and_clearing_the_session(offline_env: None) -> None:
    at = start_retail()
    at.sidebar.radio(key=CHOICE_KEY).set_value("bank").run()
    assert_clean(at)
    assert "fz-card" in texts(at.markdown)
    clear = next(b for b in at.sidebar.button if b.label == "Clear session")
    clear.click().run()
    assert_clean(at)
    assert "Choose Upload files" in texts(at.info)


def test_free_text_question_offline_gets_the_offline_message(offline_env: None) -> None:
    at = start_retail()
    at.text_input(key="fz_question").set_value("Tell me a joke about revenue")
    ask = next(b for b in at.button if b.label == "Ask")
    ask.click().run()
    assert_clean(at)
    assert "only" in texts(at.info).lower() and "demo" in texts(at.info).lower()

UPLOAD_FILES = ["sales", "products", "stores", "customers"]


def demo_upload() -> list[tuple[str, bytes, str]]:
    """The retail demo files as an upload (name, bytes, mime type)."""
    folder = APP_FILE.parents[1] / "data" / "demo" / "retail"
    return [(f"{n}.csv", (folder / f"{n}.csv").read_bytes(), "text/csv") for n in UPLOAD_FILES]


def test_upload_path_builds_removes_pii_and_shows_the_dashboard(offline_env: None) -> None:
    at = AppTest.from_file(APP_FILE, default_timeout=TIMEOUT).run()
    at.file_uploader(key="fz_upload").set_value(demo_upload()).run()
    assert_clean(at)
    assert "Confirm and build" in [b.label for b in at.button]
    confirm = next(b for b in at.button if b.label == "Confirm and build")
    confirm.click().run()
    assert_clean(at)
    assert "Dataset ready" in texts(at.success)
    assert "Removed from analysis" in texts(at.info)
    assert "customers.email" in texts(at.info)
    assert "fz-card" in texts(at.markdown)


def test_unsupported_upload_shows_a_readable_error(offline_env: None) -> None:
    at = AppTest.from_file(APP_FILE, default_timeout=TIMEOUT).run()
    broken = ("broken.xlsx", b"this is not a workbook", "application/octet-stream")
    at.file_uploader(key="fz_upload").set_value(broken).run()
    assert not at.exception
    assert "corrupt or not a valid .xlsx" in texts(at.error)


def test_bad_formula_is_rejected_and_good_formula_is_applied(offline_env: None) -> None:
    at = start_retail()
    state = at.session_state["fz_state"]
    key = f"{state.model.dataset_id}:expr:retail:revenue"
    at.text_input(key=key).set_value("SUM(").run()
    next(b for b in at.button if b.label == "Apply metric settings").click().run()
    assert not at.exception
    assert "could not be completed" in texts(at.error) or "formula" in texts(at.error).lower()
    at.text_input(key=key).set_value("SUM({amount}) * 2").run()
    next(b for b in at.button if b.label == "Apply metric settings").click().run()
    assert_clean(at)
    assert "Saved" in texts(at.success)
    assert at.session_state["fz_state"].overrides == {"revenue": "SUM({amount}) * 2"}


def test_a_crashing_tab_shows_a_reference_and_leaves_the_others_working(
    offline_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(state) -> None:
        raise RuntimeError("secret customer value 12345")

    monkeypatch.setattr(ui_audit, "render_audit_tab", broken)
    at = AppTest.from_file(APP_FILE, default_timeout=TIMEOUT).run()
    at.sidebar.radio(key=CHOICE_KEY).set_value("retail").run()
    assert not at.exception
    message = texts(at.error)
    assert "Something went wrong (ref: " in message
    assert "secret customer value" not in message
    assert "fz-card" in texts(at.markdown)


def test_password_gate_blocks_until_the_right_password(
    offline_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_PASSWORD", "open-sesame")
    get_settings.cache_clear()
    at = AppTest.from_file(APP_FILE, default_timeout=TIMEOUT).run()
    assert not at.tabs
    at.text_input(key="fz_password").set_value("wrong").run()
    assert not at.tabs and "not correct" in texts(at.error)
    at.text_input(key="fz_password").set_value("open-sesame").run()
    assert not at.exception
    assert len(at.tabs) == 5
