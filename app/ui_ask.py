"""Ask tab: questions in plain language, answered by verified SQL."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.i18n import t
from app.loading import demo_key
from app.state import SessionState, Turn
from app.theme import ACCENT, GOOD, badge_html, register_template
from app.ui_common import error_boundary, md_escape
from core.dashboard.chartspecs import dimension_options
from core.dashboard.filters import describe_filters
from core.models import FilterSpec, QueryResult
from core.query.charts import ChartSpec
from core.query.nl2sql import Answer, NL2SQLAgent, format_number

PENDING_KEY = "fz_pending_question"
MAX_TURNS = 10
SUGGESTION_COUNT = 6
CLARIFY_BUTTONS = 4


def generic_suggestions(state: SessionState) -> list[str]:
    """Suggested questions for an uploaded dataset, built from its metrics and columns."""
    model, metrics = state.model, state.metrics
    ideas = [f"What is the total {m.label.lower()}?" for m in metrics[:2]]
    if metrics and "date" in model.roles:
        ideas.append(f"Show {metrics[0].label.lower()} by month.")
    if metrics:
        for dimension in dimension_options(model)[:2]:
            name = dimension.replace("_", " ")
            ideas.append(f"Which {name} has the highest {metrics[0].label.lower()}?")
    ideas.append("How many rows are in the data?")
    return ideas[:SUGGESTION_COUNT]


def suggestions_for(state: SessionState) -> list[str]:
    """Golden questions for the demo datasets, generic ideas for uploads."""
    key = demo_key(state.source)
    if key:
        return state.gateway.offline.suggestions(key, SUGGESTION_COUNT)
    return generic_suggestions(state)


def set_pending(question: str) -> None:
    """Button callback: remember the question so the next run answers it."""
    st.session_state[PENDING_KEY] = question


def make_agent(state: SessionState) -> NL2SQLAgent:
    """The question-answering agent for the current data and metrics."""
    return NL2SQLAgent(
        state.workspace.con, state.model, state.gateway, state.settings, state.metrics,
        demo_key(state.source),
    )


def ask(state: SessionState, spec: FilterSpec, question: str) -> None:
    """Answer one question and put it at the top of the history."""
    filters_text = describe_filters(spec, state.settings.category_values_allowed)
    with st.spinner(t("ask.thinking")):
        answer = make_agent(state).ask(question, filters_text)
    state.history.insert(0, Turn(question, answer, filters_text))
    del state.history[MAX_TURNS:]


# ---------------------------------------------------------------- rendering


def result_frame(result: QueryResult) -> pd.DataFrame:
    """The result rows as a table."""
    return pd.DataFrame(result.rows, columns=result.columns)


def result_figure(frame: pd.DataFrame, chart: ChartSpec) -> go.Figure:
    """A line or bar chart chosen by ``choose_chart`` (never by model-written code)."""
    fig = go.Figure(layout={"template": register_template()})
    data = frame.head(chart.limit) if chart.limit else frame
    if chart.kind == "line":
        for column in chart.y:
            fig.add_scatter(x=data[chart.x], y=data[column], name=column, mode="lines+markers")
    elif chart.kind == "hbar":
        ordered = data.sort_values(chart.y[0])
        fig.add_bar(x=ordered[chart.y[0]], y=ordered[chart.x].astype(str), orientation="h")
    else:
        for column in chart.y:
            fig.add_bar(x=data[chart.x].astype(str), y=data[column], name=column)
        fig.update_layout(barmode="group")
    return fig


def render_result(result: QueryResult, chart: ChartSpec) -> None:
    """KPI card, chart or table, depending on the shape of the result."""
    frame = result_frame(result)
    if chart.kind == "kpi":
        st.metric(result.columns[0].replace("_", " ").capitalize(),
                  format_number(result.rows[0][0]))
    elif chart.kind == "table":
        st.dataframe(frame, hide_index=True)
    else:
        st.plotly_chart(result_figure(frame, chart))


def render_how(result: QueryResult, chart: ChartSpec, turn: Turn) -> None:
    """The "How this was computed" expander."""
    with st.expander(t("ask.how")):
        st.markdown(f"**{t('ask.sql')}**")
        st.code(result.sql, language="sql")
        metrics = ", ".join(result.metric_ids) or t("ask.no_metrics")
        st.markdown(t("ask.metrics_used", metrics=md_escape(metrics)))
        filters = md_escape(turn.filters_text or t("filters.none"))
        st.markdown(t("ask.filters_used", filters=filters))
        runtime = f"{result.runtime_ms:.0f}"
        st.markdown(t("ask.rows_runtime", rows=f"{result.row_count:,}", ms=runtime))
        if chart.kind not in ("table", "kpi"):
            st.dataframe(result_frame(result), hide_index=True)


def verification_badge(result: QueryResult) -> str:
    """HTML of the Verified / Unverified pill."""
    if result.verified:
        return badge_html(t("ask.verified"), GOOD)
    return badge_html(t("ask.unverified"), ACCENT)


def render_ok(answer: Answer, turn: Turn) -> None:
    """A successful answer: badge, text, result and the computation details."""
    result, chart = answer.result, answer.chart
    st.markdown(verification_badge(result), unsafe_allow_html=True)
    st.markdown(md_escape(answer.text))
    render_result(result, chart)
    for warning in result.warnings:
        st.caption(md_escape(warning))
    render_how(result, chart, turn)


def render_clarify(state: SessionState, turn: Turn, index: int) -> None:
    """A clarifying question with one button per metric the user can pick."""
    st.info(md_escape(turn.answer.text))
    if not state.metrics:
        return
    st.caption(t("ask.clarify_hint"))
    shown = state.metrics[:CLARIFY_BUTTONS]
    for column, metric in zip(st.columns(len(shown)), shown, strict=True):
        column.button(
            metric.label, key=f"fz_clarify:{index}:{metric.id}", on_click=set_pending,
            args=(f"{turn.question} Use the metric {metric.label}.",),
        )


def render_turn(state: SessionState, turn: Turn, index: int) -> None:
    """One question with its answer."""
    with st.container(border=True):
        st.markdown(f"**{md_escape(turn.question)}**")
        status = turn.answer.status
        if status == "ok":
            render_ok(turn.answer, turn)
        elif status == "clarify":
            render_clarify(state, turn, index)
        elif status == "refuse":
            st.warning(md_escape(turn.answer.text))
        else:
            st.info(md_escape(turn.answer.text))


def render_suggestions(state: SessionState) -> None:
    """Suggested questions as buttons."""
    st.caption(t("ask.suggested"))
    ideas = suggestions_for(state)
    grid = st.columns(2)
    for index, question in enumerate(ideas):
        grid[index % 2].button(
            question, key=f"fz_suggest:{index}", on_click=set_pending, args=(question,)
        )


def render_ask_tab(state: SessionState, spec: FilterSpec, rows: int) -> None:
    """The Ask tab: suggestions, a question box and the answers so far."""
    if state.model is None:
        st.info(t("common.need_data"))
        return
    if state.gateway.is_offline:
        st.caption(t("ask.offline_note"))
    render_suggestions(state)
    with st.form("fz_ask_form", clear_on_submit=True):
        typed = st.text_input(t("ask.placeholder"), key="fz_question")
        submitted = st.form_submit_button(t("ask.submit"))
    question = st.session_state.pop(PENDING_KEY, None) or (typed.strip() if submitted else "")
    if question:
        with error_boundary(t("area.ask")):
            ask(state, spec, question)
    for index, turn in enumerate(state.history):
        render_turn(state, turn, index)
