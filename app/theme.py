"""One palette and one Plotly template for the whole app, plus the static KPI card HTML.

Colors work on light and dark backgrounds: cards and charts use transparent fills and
semi-transparent grid lines, and text inherits the page color.
"""

from __future__ import annotations

from html import escape

import plotly.graph_objects as go
import plotly.io as pio

from core.dashboard.kpi import KpiValue

PRIMARY = "#3B6FE0"
ACCENT = "#F59E0B"
GOOD = "#16A34A"
BAD = "#DC2626"
NEUTRAL = "#6B7280"
SERIES = [PRIMARY, ACCENT, "#14B8A6", "#8B5CF6", "#EC4899", "#64748B"]
GRID = "rgba(128, 128, 128, 0.25)"
TEMPLATE_NAME = "finalyzer"
TONE_COLORS = {"good": GOOD, "bad": BAD, "neutral": NEUTRAL}

CSS = """
<style>
.fz-card { border: 1px solid rgba(128,128,128,0.35); border-radius: 10px; padding: 12px 14px;
  margin-bottom: 10px; background: rgba(128,128,128,0.06); }
.fz-label { font-size: 0.85rem; opacity: 0.75; }
.fz-value { font-size: 1.7rem; font-weight: 650; line-height: 1.25; }
.fz-delta { font-size: 0.9rem; font-weight: 600; }
.fz-delta span { font-weight: 400; opacity: 0.7; }
.fz-badge { display: inline-block; border-radius: 999px; padding: 2px 10px; font-size: 0.8rem;
  font-weight: 600; border: 1px solid currentColor; }
</style>
"""


def register_template() -> str:
    """Register the Plotly template once and return its name."""
    if TEMPLATE_NAME not in pio.templates:
        pio.templates[TEMPLATE_NAME] = go.layout.Template(
            layout={
                "colorway": SERIES,
                "paper_bgcolor": "rgba(0,0,0,0)",
                "plot_bgcolor": "rgba(0,0,0,0)",
                "font": {"family": "sans-serif", "size": 13},
                "margin": {"l": 10, "r": 10, "t": 50, "b": 10},
                "xaxis": {"gridcolor": GRID, "zerolinecolor": GRID},
                "yaxis": {"gridcolor": GRID, "zerolinecolor": GRID},
                "legend": {"orientation": "h", "y": -0.2},
            }
        )
    return TEMPLATE_NAME


def arrow(delta: float | None) -> str:
    """Direction symbol for a change."""
    if delta is None or delta == 0:
        return "•"
    return "▲" if delta > 0 else "▼"


def kpi_card_html(kpi: KpiValue, comparison_text: str) -> str:
    """HTML of one KPI card. Every dynamic piece is escaped."""
    color = TONE_COLORS.get(kpi.tone, NEUTRAL)
    return (
        '<div class="fz-card">'
        f'<div class="fz-label">{escape(kpi.label)}</div>'
        f'<div class="fz-value">{escape(kpi.display)}</div>'
        f'<div class="fz-delta" style="color:{color}">{arrow(kpi.delta)} '
        f"{escape(kpi.delta_display)} <span>{escape(comparison_text)}</span></div>"
        "</div>"
    )


def badge_html(text: str, color: str) -> str:
    """A small colored pill (used for Verified / Unverified / AI-written labels)."""
    return f'<span class="fz-badge" style="color:{escape(color)}">{escape(text)}</span>'
