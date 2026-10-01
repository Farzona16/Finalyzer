"""Audit tab: every attempted call to an AI service, exactly as it was (or would have been) sent."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.i18n import t
from app.state import SessionState
from core.llm.audit import AuditLog
from core.models import AuditEvent


def events_frame(events: list[AuditEvent]) -> pd.DataFrame:
    """One row per event with the columns a reader needs to judge it."""
    return pd.DataFrame(
        {
            t("audit.col_time"): [e.ts for e in events],
            t("audit.col_purpose"): [e.purpose for e in events],
            t("audit.col_mode"): [e.mode for e in events],
            t("audit.col_model"): [e.provider_model for e in events],
            t("audit.col_bytes"): [e.payload_bytes for e in events],
            t("audit.col_outside"): [e.external for e in events],
            t("audit.col_blocked"): [e.blocked for e in events],
            t("audit.col_reason"): [e.block_reason or e.error or "" for e in events],
            t("audit.col_tokens"): [e.tokens_in + e.tokens_out for e in events],
            t("audit.col_latency"): [round(e.latency_ms) for e in events],
        }
    )


def render_counters(audit: AuditLog) -> None:
    """Big counters: bytes that left the private network, blocked calls, tokens."""
    left, middle, right = st.columns(3)
    left.metric(t("audit.bytes_outside"), f"{audit.total_bytes_sent:,}")
    middle.metric(t("audit.blocked"), f"{audit.blocked_count:,}")
    right.metric(t("audit.tokens"), f"{audit.tokens_used:,}")


def render_payload_viewer(events: list[AuditEvent]) -> None:
    """Pick one event and show its exact payload."""
    labels = [f"{i + 1}. {e.ts} - {e.purpose}" + (" (blocked)" if e.blocked else "")
              for i, e in enumerate(events)]
    chosen = st.selectbox(t("audit.pick"), range(len(events)), format_func=lambda i: labels[i],
                          key="fz_audit_pick")
    event = events[chosen]
    if event.block_reason:
        st.warning(t("audit.block_reason", reason=event.block_reason))
    st.code(event.payload_text, language="json", wrap_lines=True)


def render_audit_tab(state: SessionState) -> None:
    """The whole Audit tab."""
    audit = state.audit
    st.caption(t("audit.intro"))
    render_counters(audit)
    events = audit.events
    if not events:
        st.info(t("audit.none"))
        return
    st.dataframe(events_frame(events), hide_index=True)
    render_payload_viewer(events)
    st.download_button(
        t("audit.download"), data=audit.export_jsonl(), file_name="finalyzer_audit.jsonl",
        mime="application/x-ndjson",
    )
