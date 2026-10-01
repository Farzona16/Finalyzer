"""Insights tab: findings as cards, "More findings" and "Next steps"."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.i18n import t
from app.state import SessionState
from app.theme import ACCENT, BAD, GOOD, NEUTRAL, badge_html
from app.ui_common import error_boundary, md_escape
from app.ui_dashboard import metric_key
from core.dashboard.filters import filter_hash
from core.insights.pipeline import InsightBundle, build_insights
from core.models import FilterSpec, Finding, RecommendationCard

LEVEL_COLORS = {"High": BAD, "Medium": ACCENT, "Low": NEUTRAL}
CONFIDENCE_COLORS = {"High": GOOD, "Medium": ACCENT, "Low": NEUTRAL}


def insights_key(state: SessionState, spec: FilterSpec) -> str:
    """Cache key: results are valid for one dataset build, one filter state, one metric set."""
    model = state.model
    mode = f"{state.settings.privacy_mode}:{state.gateway.is_offline}"
    return "|".join([model.dataset_id, model.version, filter_hash(spec),
                     metric_key(state.metrics), mode])


def run_insights(state: SessionState, key: str) -> None:
    """Compute detectors, cards and next steps for the current view and keep them."""
    bundle = build_insights(
        state.workspace.con, state.model, state.metrics, state.primary, state.unavailable,
        state.gateway, state.settings,
    )
    state.insights = {key: bundle}


def facts_table(finding: Finding) -> pd.DataFrame:
    """The numbers behind a card, as a small table."""
    rows = [(k.replace("_", " "), str(v)) for k, v in finding.facts.items()]
    return pd.DataFrame(rows, columns=[t("insights.fact"), t("insights.value")])


def render_card(card: RecommendationCard, finding: Finding | None) -> None:
    """One insight card."""
    with st.container(border=True):
        st.markdown(f"**{md_escape(card.observation)}**")
        badges = (
            badge_html(t("insights.priority", level=card.priority), LEVEL_COLORS[card.priority])
            + " "
            + badge_html(
                t("insights.confidence", level=card.confidence), CONFIDENCE_COLORS[card.confidence]
            )
        )
        st.markdown(badges, unsafe_allow_html=True)
        for line in card.evidence:
            st.markdown(f"- {md_escape(line)}")
        st.markdown(f"**{t('insights.cause')}** {md_escape(card.likely_cause)}")
        st.markdown(f"**{t('insights.actions')}**")
        for action in card.actions:
            st.markdown(f"- {md_escape(action)}")
        if card.track_metric:
            st.caption(t("insights.track", metric=md_escape(card.track_metric)))
        if finding is not None:
            with st.expander(t("insights.how")):
                st.caption(t("insights.detector", kind=finding.type.replace("_", " "),
                             rows=f"{finding.support:,}"))
                st.dataframe(facts_table(finding), hide_index=True)


def render_cards(bundle: InsightBundle) -> None:
    """Top findings, then the rest under "More findings"."""
    by_id = {f.id: f for f in bundle.report.findings}
    cards = bundle.narration.cards
    top, rest = cards[: bundle.top_count], cards[bundle.top_count :]
    for card in top:
        render_card(card, by_id.get(card.finding_id))
    if rest:
        with st.expander(t("insights.more", count=len(rest))):
            for card in rest:
                render_card(card, by_id.get(card.finding_id))


def render_next_steps(bundle: InsightBundle) -> None:
    """The three deterministic blocks of "Next steps"."""
    steps = bundle.next_steps
    st.subheader(t("insights.next_steps"))
    blocks = [
        (t("insights.missing"), steps.missing),
        (t("insights.track_title"), steps.track),
        (t("insights.quality"), steps.quality),
    ]
    for title, lines in blocks:
        if lines:
            st.markdown(f"**{title}**")
            for line in lines:
                st.markdown(f"- {md_escape(line)}")


def render_bundle(bundle: InsightBundle) -> None:
    """Badge, notes, cards and next steps."""
    source = bundle.narration.source
    label = t("insights.ai") if source == "ai" else t("insights.template")
    st.markdown(badge_html(label, GOOD if source == "ai" else NEUTRAL), unsafe_allow_html=True)
    if bundle.narration.note:
        st.caption(md_escape(bundle.narration.note))
    for note in bundle.report.notes:
        st.caption(md_escape(note))
    if bundle.report.last_complete_month:
        st.caption(t("insights.month", month=bundle.report.last_complete_month))
    if not bundle.narration.cards:
        st.info(t("insights.none"))
    render_cards(bundle)
    render_next_steps(bundle)


def render_insights_tab(state: SessionState, spec: FilterSpec, rows: int) -> None:
    """The Insights tab: a button computes the findings for the current filters."""
    if state.model is None:
        st.info(t("common.need_data"))
        return
    if rows == 0:
        st.info(t("dash.empty"))
        return
    key = insights_key(state, spec)
    if st.button(t("insights.run"), key="fz_run_insights", type="primary"):
        with st.spinner(t("insights.running")), error_boundary(t("area.insights")):
            run_insights(state, key)
    bundle = state.insights.get(key)
    if bundle is None:
        st.info(t("insights.prompt"))
        return
    with error_boundary(t("area.insights")):
        render_bundle(bundle)
