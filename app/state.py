"""Everything that belongs to one browser session, kept in one object in ``st.session_state``."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
import streamlit as st

from core.config import Settings, get_settings
from core.ingest.flatten import Workspace
from core.insights.pipeline import InsightBundle
from core.llm.audit import AuditLog
from core.llm.gateway import LLMGateway
from core.models import DatasetModel, Metric, Relationship
from core.query.nl2sql import Answer

STATE_KEY = "fz_state"
CHOICE_KEY = "fz_source_choice"
AUTH_KEY = "fz_authenticated"
KEEP_ON_CLEAR = {AUTH_KEY, "fz_password"}  # clearing data should not log the visitor out


@dataclass
class Turn:
    """One question and its answer in the Ask tab."""

    question: str
    answer: Answer
    filters_text: str


@dataclass
class SessionState:
    """The loaded data, the built model, the chosen metrics and cached results."""

    settings: Settings
    audit: AuditLog
    gateway: LLMGateway
    source: str | None = None
    raw_tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    upload_signature: str = ""
    remove_duplicates: bool = False
    workspace: Workspace | None = None
    workspace_id: str = ""
    model: DatasetModel | None = None
    relationships: list[Relationship] = field(default_factory=list)
    pack_name: str | None = None
    overrides: dict[str, str] = field(default_factory=dict)
    metrics: list[Metric] = field(default_factory=list)
    primary: Metric | None = None
    unavailable: list = field(default_factory=list)
    insights: dict[str, InsightBundle] = field(default_factory=dict)
    history: list[Turn] = field(default_factory=list)
    preview: dict[str, tuple] = field(default_factory=dict)
    load_error: str = ""
    notice: str = ""

    def close_workspace(self) -> None:
        """Release the DuckDB connection of the current data."""
        if self.workspace is not None:
            self.workspace.con.close()
        self.workspace = None
        self.model = None

    def reset_results(self) -> None:
        """Forget answers and insights (the data or the metrics changed)."""
        self.insights.clear()
        self.history.clear()


def new_state() -> SessionState:
    """A fresh session: settings from the environment, an empty audit log, a gateway."""
    settings = get_settings()
    audit = AuditLog()
    return SessionState(settings=settings, audit=audit, gateway=LLMGateway(settings, audit))


def get_state() -> SessionState:
    """The session's state object (created on first use)."""
    if STATE_KEY not in st.session_state:
        st.session_state[STATE_KEY] = new_state()
    return st.session_state[STATE_KEY]


def clear_session() -> None:
    """Callback of "Clear session": close the connection and forget everything."""
    state = st.session_state.get(STATE_KEY)
    if state is not None:
        state.close_workspace()
        state.audit.clear()
    for key in list(st.session_state.keys()):
        if key not in KEEP_ON_CLEAR:
            del st.session_state[key]
    st.cache_data.clear()
