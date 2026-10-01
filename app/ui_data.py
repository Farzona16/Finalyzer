"""Data tab: upload, cleaning and PII reports, fact table, joins, roles and metrics."""

from __future__ import annotations

import hashlib
import uuid

import pandas as pd
import streamlit as st

from app.i18n import t
from app.loading import (
    ROLE_ORDER,
    build_model,
    open_upload,
    order_metrics,
    preview_columns,
    read_uploads,
)
from app.state import SessionState
from app.ui_common import error_boundary, md_escape, mode_banner
from core.errors import describe_exception
from core.ingest.flatten import pick_fact_table, propose
from core.metrics.registry import choose_pack, evaluate_pack, list_pack_names, validate_expression
from core.models import ColumnInfo, Relationship

NO_ROLE = "-"
UPLOAD_TYPES = ["csv", "xlsx", "xlsm"]


# ---------------------------------------------------------------- upload


def upload_signature(files: list, remove_duplicates: bool) -> str:
    """Identifies an upload by file names, sizes and the duplicate option."""
    text = "|".join(f"{f.name}:{f.size}" for f in files) + f"|{remove_duplicates}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def load_uploaded(state: SessionState, files: list, remove_duplicates: bool) -> None:
    """Read the files, clean them and open a new workspace (errors go to ``load_error``)."""
    state.close_workspace()
    state.metrics, state.primary, state.unavailable = [], None, []
    state.preview.clear()
    state.reset_results()
    state.load_error = ""
    try:
        raw = read_uploads([(f.name, f.getvalue()) for f in files], state.settings)
        state.raw_tables = raw
        state.workspace = open_upload(raw, remove_duplicates, state.settings)
        state.workspace_id = uuid.uuid4().hex[:8]
    except Exception as exc:  # noqa: BLE001 - shown to the user with a reference id
        state.load_error = describe_exception(exc)


def render_upload(state: SessionState) -> None:
    """File uploader and the duplicate-rows option; loads the data when they change."""
    limits = t("data.upload_help", mb=state.settings.max_file_mb,
               rows=f"{state.settings.max_rows:,}")
    files = st.file_uploader(
        t("data.upload"), type=UPLOAD_TYPES, accept_multiple_files=True, key="fz_upload",
        help=limits,
    )
    dups = st.checkbox(t("data.remove_dups"), value=False, key="fz_dups")
    if files:
        signature = upload_signature(files, dups)
        if signature != state.upload_signature:
            state.upload_signature = signature
            with st.spinner(t("data.loading")):
                load_uploaded(state, files, dups)
    if state.load_error:
        st.error(state.load_error)


# ---------------------------------------------------------------- reports


def pii_items(state: SessionState) -> list[str]:
    """``table.column (reason)`` for every personal-data column found."""
    if state.model is not None and state.model.dropped_pii:
        return list(state.model.dropped_pii)
    ws = state.workspace
    return [f"{table}.{col} ({why})" for table, cols in ws.pii.items() for col, why in cols.items()]


def render_tables(state: SessionState) -> None:
    """Table list, cleaning report and personal-data report."""
    ws = state.workspace
    st.subheader(t("data.tables"))
    st.dataframe(
        pd.DataFrame(
            {
                t("data.col_table"): list(ws.row_counts),
                t("data.col_rows"): list(ws.row_counts.values()),
                t("data.col_columns"): [len(ws.profiles[name]) for name in ws.row_counts],
            }
        ),
        hide_index=True,
    )
    with st.expander(t("data.cleaning")):
        if ws.cleaning_report:
            st.dataframe(
                pd.DataFrame([a.model_dump() for a in ws.cleaning_report])[
                    ["table", "column", "action", "count", "detail"]
                ],
                hide_index=True,
            )
        else:
            st.caption(t("data.cleaning_none"))
        for warning in ws.warnings:
            st.warning(md_escape(warning))
    items = pii_items(state)
    if items:
        key = "data.pii_removed" if state.model is not None else "data.pii_found"
        st.info(t(key, items=md_escape(", ".join(items))))


# ---------------------------------------------------------------- build form


def relationship_label(rel: Relationship) -> str:
    """Text of one join checkbox."""
    return t(
        "data.rel", fact_col=rel.fact_col, table=rel.dim_table, dim_col=rel.dim_col,
        pct=f"{rel.containment:.0%}",
    )


def choose_relationships(state: SessionState, fact: str) -> list[Relationship]:
    """One checkbox per proposed join; proposals with containment of 0.95 or more start ticked."""
    ws = state.workspace
    cache_key = f"rels:{fact}"
    if cache_key not in state.preview:
        state.preview[cache_key] = (propose(ws, fact),)
    proposals: list[Relationship] = state.preview[cache_key][0]
    if not proposals:
        st.caption(t("data.no_rels"))
    selected = []
    for rel in proposals:
        key = f"{state.workspace_id}:rel:{fact}:{rel.fact_col}:{rel.dim_table}"
        if st.checkbox(relationship_label(rel), value=rel.containment >= 0.95, key=key):
            selected.append(rel)
    return selected


def cached_preview(
    state: SessionState, fact: str, rels: list[Relationship]
) -> tuple[str, list[ColumnInfo], dict[str, str]]:
    """Columns and detected roles of the future flat view (computed once per selection)."""
    signature = fact + "|" + ",".join(f"{r.fact_col}>{r.dim_table}.{r.dim_col}" for r in rels)
    key = f"preview:{signature}"
    if key not in state.preview:
        columns, roles = preview_columns(state.workspace, fact, rels)
        state.preview[key] = (columns, roles)
    columns, roles = state.preview[key]
    return signature, columns, roles


def choose_roles(
    state: SessionState, signature: str, columns: list[ColumnInfo], detected: dict[str, str]
) -> dict[str, str]:
    """One selector per role, pre-filled with the detected column."""
    options = [NO_ROLE, *[c.name for c in columns if not c.is_pii]]
    tag = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:8]
    chosen: dict[str, str] = {}
    grid = st.columns(3)
    for index, role in enumerate(ROLE_ORDER):
        default = detected.get(role)
        value = grid[index % 3].selectbox(
            t(f"role.{role}"), options, index=options.index(default) if default in options else 0,
            key=f"{state.workspace_id}:role:{tag}:{role}",
        )
        if value != NO_ROLE:
            chosen[role] = value
    return chosen


def confirm_build(
    state: SessionState, fact: str, rels: list[Relationship], roles: dict[str, str]
) -> None:
    """Create ``base``/``data`` and reset everything that depended on the previous build."""
    state.model = build_model(state.workspace, fact, rels, roles)
    state.relationships = rels
    state.pack_name, state.overrides = None, {}
    state.metrics, state.primary, state.unavailable = order_metrics(state.model, None, {})
    state.reset_results()
    st.session_state.pop("fz_filters_notice", None)


def render_build(state: SessionState) -> None:
    """Fact table, joins and roles, then the "Confirm and build" button."""
    ws = state.workspace
    st.subheader(t("data.build"))
    tables = list(ws.profiles)
    default_fact = state.model.fact_table if state.model else pick_fact_table(ws)
    fact = st.selectbox(
        t("data.fact"), tables, index=tables.index(default_fact),
        key=f"{state.workspace_id}:fact",
    )
    rels = choose_relationships(state, fact)
    signature, columns, detected = cached_preview(state, fact, rels)
    st.markdown(f"**{t('data.roles')}**")
    st.caption(t("data.roles_help"))
    roles = choose_roles(state, signature, columns, detected)
    if st.button(t("data.confirm"), type="primary", key=f"{state.workspace_id}:confirm"):
        with st.spinner(t("data.building")), error_boundary(t("area.build")):
            confirm_build(state, fact, rels, roles)
            state.notice = t("data.built", rows=f"{state.model.row_count:,}")
        # Run the page again so the sidebar slicers and all tabs see the new dataset.
        if state.notice:
            st.rerun()


# ---------------------------------------------------------------- metrics


def edited_expressions(state: SessionState, pack: str) -> dict[str, str]:
    """Expressions the user changed in the text boxes (compared with the pack's own)."""
    model = state.model
    edited = {}
    for status in evaluate_pack(model, pack, None):
        key = f"{model.dataset_id}:expr:{pack}:{status.metric.id}"
        text = st.session_state.get(key)
        if text is not None and text.strip() != status.metric.expr.strip():
            edited[status.metric.id] = text
    return edited


def render_metric_rows(state: SessionState, pack: str) -> None:
    """One row per metric: availability and an editable formula."""
    model = state.model
    for status in evaluate_pack(model, pack, state.overrides):
        metric = status.metric
        left, right = st.columns([2, 5])
        badge = t("data.metric_ok") if status.available else t("data.metric_missing")
        left.markdown(f"**{md_escape(metric.label)}**  \n{badge}")
        if not status.available:
            left.caption(md_escape(status.reason))
        right.text_input(
            t("data.formula"), value=metric.expr, label_visibility="collapsed",
            key=f"{model.dataset_id}:expr:{pack}:{metric.id}",
        )


def apply_metric_changes(state: SessionState, pack: str) -> None:
    """Validate every edited formula on the data; accept all or none."""
    edited = edited_expressions(state, pack)
    checked = {mid: validate_expression(state.workspace.con, state.model, text)
               for mid, text in edited.items()}
    state.pack_name, state.overrides = pack, checked
    state.metrics, state.primary, state.unavailable = order_metrics(state.model, pack, checked)
    state.reset_results()


def render_metrics(state: SessionState) -> None:
    """Metric pack selector, metric list with editable formulas and the apply button."""
    model = state.model
    st.subheader(t("data.metrics"))
    packs = list_pack_names()
    current = state.pack_name or choose_pack(model)
    pack = st.selectbox(
        t("data.pack"), packs, index=packs.index(current), key=f"{model.dataset_id}:pack",
    )
    st.caption(t("data.formula_help"))
    render_metric_rows(state, pack)
    if st.button(t("data.apply_metrics"), key=f"{model.dataset_id}:apply"):
        with error_boundary(t("area.metrics")):
            apply_metric_changes(state, pack)
            st.success(t("data.metrics_saved", count=len(state.metrics)))


# ---------------------------------------------------------------- tab


def render_data_tab(state: SessionState) -> None:
    """The whole Data tab."""
    mode_banner(state.settings)
    if state.notice:
        st.success(state.notice)
        state.notice = ""
    if state.source == "upload":
        render_upload(state)
    if state.workspace is None:
        if state.source != "upload" and state.source is not None:
            st.info(t("data.demo_failed"))
        elif not state.load_error:
            st.info(t("data.choose"))
        return
    render_tables(state)
    render_build(state)
    if state.model is not None:
        render_metrics(state)
