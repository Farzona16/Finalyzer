"""Streamlit entry point. Run with: ``streamlit run app/main.py``."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from app.i18n import t  # noqa: E402
from app.state import SessionState, get_state  # noqa: E402
from app.ui_ask import render_ask_tab  # noqa: E402
from app.ui_audit import render_audit_tab  # noqa: E402
from app.ui_common import error_boundary, password_gate  # noqa: E402
from app.ui_dashboard import render_dashboard, render_filter_summary  # noqa: E402
from app.ui_data import render_data_tab  # noqa: E402
from app.ui_insights import render_insights_tab  # noqa: E402
from app.ui_sidebar import render_sidebar  # noqa: E402
from core.dashboard.filters import apply_filters  # noqa: E402
from core.errors import UserFacingError  # noqa: E402
from core.logging_setup import setup_logging  # noqa: E402
from core.models import FilterSpec  # noqa: E402


def load_state() -> SessionState:
    """The session state, or a readable message and a stop when the configuration is invalid."""
    try:
        return get_state()
    except UserFacingError as exc:
        st.error(exc.message)
        st.stop()


def apply_current_filters(state: SessionState, spec: FilterSpec) -> int:
    """Rebuild the ``data`` view for the active slicers; returns the number of rows left."""
    if state.model is None:
        return 0
    con = state.workspace.con
    try:
        return apply_filters(con, spec, state.model)
    except UserFacingError as exc:
        st.sidebar.error(exc.message)
        return apply_filters(con, FilterSpec(), state.model)


def render_dashboard_tab(state: SessionState, spec: FilterSpec, rows: int) -> None:
    """The Dashboard tab (its own module draws the content)."""
    if state.model is None:
        st.info(t("common.need_data"))
        return
    render_filter_summary(state.model, spec, rows)
    render_dashboard(state.workspace.con, state.model, state.metrics, spec, state.settings, rows)


def main() -> None:
    """Draw the page: password gate, sidebar, then one tab per area."""
    st.set_page_config(page_title=t("app.title"), layout="wide")
    state = load_state()
    setup_logging(state.settings.log_level)
    if not password_gate(state.settings):
        st.stop()
    spec = render_sidebar(state)
    rows = apply_current_filters(state, spec)
    st.caption(t("app.subtitle"))
    data_tab, dash_tab, insights_tab, ask_tab, audit_tab = st.tabs(
        [t("tabs.data"), t("tabs.dashboard"), t("tabs.insights"), t("tabs.ask"), t("tabs.audit")]
    )
    with data_tab, error_boundary(t("tabs.data")):
        render_data_tab(state)
    with dash_tab, error_boundary(t("tabs.dashboard")):
        render_dashboard_tab(state, spec, rows)
    with insights_tab, error_boundary(t("tabs.insights")):
        render_insights_tab(state, spec, rows)
    with ask_tab, error_boundary(t("tabs.ask")):
        render_ask_tab(state, spec, rows)
    with audit_tab, error_boundary(t("tabs.audit")):
        render_audit_tab(state)


main()
