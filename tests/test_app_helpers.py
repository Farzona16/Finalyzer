"""Helpers behind the UI that do not need a browser: loading, previews, text safety."""

from __future__ import annotations

import pytest

from app.loading import order_metrics, preview_columns
from app.ui_common import md_escape, password_ok
from core.config import Settings
from core.insights.pipeline import build_insights
from core.llm.gateway import LLMGateway
from core.metrics.registry import primary_metric
from tests.fakes import make_settings
from tests.helpers import DemoDataset


def test_preview_does_not_touch_base_or_data(retail: DemoDataset) -> None:
    before = retail.con.execute("SELECT COUNT(*) FROM base").fetchone()[0]
    columns, roles = preview_columns(retail.workspace, "sales", [])
    assert "amount" in {c.name for c in columns}
    assert roles["amount"] == "amount"
    assert retail.con.execute("SELECT COUNT(*) FROM base").fetchone()[0] == before
    assert "store_name" in {c.name for c in retail.model.columns}  # joined columns still there


def test_preview_with_joins_lists_dimension_columns(retail: DemoDataset) -> None:
    columns, _ = preview_columns(retail.workspace, "sales", retail.model.relationships)
    names = {c.name for c in columns}
    assert {"store_name", "category"} <= names
    assert "email" not in names or all(c.is_pii for c in columns if c.name == "email")


def test_primary_metric_comes_first(retail: DemoDataset, bank: DemoDataset) -> None:
    for demo in (retail, bank):
        metrics, primary, unavailable = order_metrics(demo.model, None, {})
        assert metrics[0].id == primary.id == primary_metric(demo.model).id
        assert len({m.id for m in metrics}) == len(metrics)
        assert all(not s.available for s in unavailable)


def test_pipeline_builds_cards_and_next_steps_without_any_llm_call(retail: DemoDataset) -> None:
    metrics, primary, unavailable = order_metrics(retail.model, None, {})
    settings = make_settings(llm_mode="offline")
    gateway = LLMGateway(settings)
    bundle = build_insights(
        retail.con, retail.model, metrics, primary, unavailable, gateway, settings
    )
    assert bundle.narration.source == "template"
    assert len(bundle.narration.cards) == len(bundle.report.findings) > 5
    assert bundle.next_steps.track
    assert gateway.audit.events == []


@pytest.mark.parametrize(
    "text, forbidden",
    [
        ("[click](http://evil.example)", "["),
        ("Revenue $5 and $6", "$5 and $6"),
        ("**bold** _it_ `code`", "**bold**"),
        ("<b>tag</b>", "<b>"),
    ],
)
def test_markdown_is_escaped(text: str, forbidden: str) -> None:
    escaped = md_escape(text)
    assert forbidden not in escaped.replace("\\" + forbidden[0], "")
    assert escaped.count("\\") >= 1


def test_plain_text_is_unchanged() -> None:
    assert md_escape("Store 03 fell 12% in 2026-09.") == "Store 03 fell 12% in 2026-09."


def test_password_comparison() -> None:
    settings = Settings(_env_file=None, app_password="s3cret")
    assert password_ok(settings, "s3cret")
    assert not password_ok(settings, "s3cre")
    assert not password_ok(settings, "")
    assert not password_ok(settings, "S3CRET")
