"""Sidebar: privacy badge, dataset selector, slicers and "Clear session"."""

from __future__ import annotations

import uuid

import streamlit as st

from app.i18n import t
from app.loading import order_metrics
from app.state import CHOICE_KEY, SessionState, clear_session
from app.ui_common import error_boundary, privacy_badge
from app.ui_dashboard import render_sidebar_filters
from core.ingest.demo import DEMO_NAMES, build_demo_model, load_demo
from core.models import FilterSpec

SOURCES = {
    "upload": "sidebar.src_upload",
    "retail": "sidebar.src_retail",
    "bank": "sidebar.src_bank",
}


def use_demo(state: SessionState, name: str) -> None:
    """Load a demo dataset and build it with the default choices."""
    state.workspace = load_demo(name, state.settings)
    state.workspace_id = uuid.uuid4().hex[:8]
    state.model = build_demo_model(state.workspace)
    state.relationships = list(state.model.relationships)
    state.pack_name, state.overrides = None, {}
    state.metrics, state.primary, state.unavailable = order_metrics(state.model, None, {})


def switch_source(state: SessionState, choice: str) -> None:
    """React to a change of the dataset selector (does nothing when it did not change)."""
    if choice == state.source:
        return
    state.close_workspace()
    state.raw_tables, state.upload_signature, state.load_error = {}, "", ""
    state.metrics, state.primary, state.unavailable = [], None, []
    state.preview.clear()
    state.reset_results()
    state.source = choice
    if choice in DEMO_NAMES:
        with st.spinner(t("sidebar.loading")), error_boundary(t("area.load")):
            use_demo(state, choice)


def render_mode_info(state: SessionState) -> None:
    """Privacy badge and a one-line description of where questions are answered."""
    st.markdown(privacy_badge(state.settings), unsafe_allow_html=True)
    if state.gateway.is_offline:
        st.caption(t("sidebar.ai_offline"))
    else:
        st.caption(t("sidebar.ai_live", model=state.gateway.primary_model))


def render_sidebar(state: SessionState) -> FilterSpec:
    """Draw the whole sidebar and return the active slicers."""
    with st.sidebar:
        st.title(t("app.title"))
        render_mode_info(state)
        choice = st.radio(
            t("sidebar.dataset"), list(SOURCES), format_func=lambda k: t(SOURCES[k]),
            key=CHOICE_KEY,
        )
        switch_source(state, choice)
        spec = FilterSpec()
        if state.model is not None:
            spec = render_sidebar_filters(state.model)
        st.divider()
        st.button(t("sidebar.clear"), on_click=clear_session)
        st.caption(t("sidebar.clear_help"))
    return spec
