"""KPI card HTML is built only from escaped strings; figures build from plain data."""

from __future__ import annotations

import pandas as pd

from app.i18n.en import TEXT
from app.theme import badge_html, kpi_card_html, register_template
from app.ui_dashboard import breakdown_figure, top_table_display, trend_figure
from core.dashboard.kpi import KpiValue, build_kpi
from core.models import Metric


def card(label: str, display: str) -> KpiValue:
    kpi = build_kpi(Metric(id="m", label=label, expr="COUNT(*)", unit="count"), 5.0, 4.0)
    return kpi.model_copy(update={"label": label, "display": display})


def test_kpi_card_escapes_every_dynamic_string() -> None:
    html = kpi_card_html(card("<script>alert(1)</script>", "<b>5</b>"), "vs <i>previous</i>")
    assert "<script>" not in html and "<b>" not in html and "<i>" not in html
    assert "&lt;script&gt;" in html


def test_badge_escapes_text() -> None:
    assert "<img" not in badge_html("<img src=x>", "#000")


def test_template_registers_once() -> None:
    assert register_template() == register_template() == "finalyzer"


def test_figures_are_built_from_frames() -> None:
    metric = Metric(id="revenue", label="Revenue", expr="SUM({amount})", unit="currency")
    trend = pd.DataFrame(
        {"period": pd.date_range("2026-01-01", periods=3, freq="MS"), "value": [1.0, 2.0, 3.0],
         "previous_year_value": [0.5, None, 2.5]}
    )
    assert len(trend_figure(trend, metric, overlay=True).data) == 2
    assert len(trend_figure(trend, metric, overlay=False).data) == 1
    top = pd.DataFrame({"category": ["a", "b"], "value": [3.0, 1.0], "share_pct": [75.0, 25.0]})
    assert breakdown_figure(top, metric, "store_name").data[0].orientation == "h"
    table = top_table_display(top, metric, "store_name")
    assert list(table.columns) == ["Store name", "Revenue", "Share"]
    assert table["Share"].tolist() == ["75.0%", "25.0%"]


def test_texts_have_no_forbidden_claims() -> None:
    joined = " ".join(TEXT.values()).lower()
    for word in ("gdpr", "hipaa", "compliant", "certified"):
        assert word not in joined
