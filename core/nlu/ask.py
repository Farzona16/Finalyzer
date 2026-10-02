"""Ask v2: router first, deterministic SQL second, v2 LLM path as fallback (spec 7)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime

import duckdb

from core.config import Settings
from core.errors import UserFacingError, ValidationError
from core.llm.gateway import LLMGateway
from core.metrics.registry import available_metrics, primary_metric
from core.metrics.timeintel import date_column
from core.models import DatasetModel, Metric
from core.nlu.compiler import compile_sql
from core.nlu.entities import Catalog
from core.nlu.followups import followups
from core.nlu.plain_answer import (
    PlainAnswer,
    clarify_answer,
    compose,
    failed_answer,
    refuse_answer,
)
from core.nlu.refuse import refuse_reason, refuse_text
from core.nlu.router import Route, route_question, should_use_llm
from core.query.executor import execute_query
from core.query.nl2sql import Answer, NL2SQLAgent
from core.query.prompts import MAX_QUESTION_CHARS


def latest_date(con: duckdb.DuckDBPyConnection, roles: dict[str, str]) -> date | None:
    """The last date present in ``data``, if a date role is mapped."""
    if "date" not in roles:
        return None
    column = date_column(roles)
    value = con.execute(f"SELECT MAX({column}) FROM data").fetchone()[0]
    if value is None:
        return None
    return value.date() if isinstance(value, datetime) else value


def from_nl(answer: Answer, locale: str, level: str) -> PlainAnswer:
    """Wrap a v2 LLM (or offline-golden) answer in the v3 shape.

    ``level`` is accepted so the LLM path matches ``compose``; SQL is always attached.
    """
    _ = level
    if answer.status == "clarify":
        return PlainAnswer(
            status="clarify", headline=answer.text, explanation=answer.clarification or ""
        )
    if answer.status == "refuse":
        return refuse_answer(answer.text)
    if answer.status != "ok" or answer.result is None:
        return failed_answer(answer.text, locale)
    result = answer.result
    how = (
        "The model wrote a SQL candidate; code validated it and ran it on this dataset."
        if locale == "en"
        else "Model SQL taklif qildi; kod tekshirib shu ma'lumotlarda bajardi."
    )
    return PlainAnswer(
        status="ok",
        headline=answer.text,
        explanation=answer.text,
        details=how,
        badge=frame_badge(locale, result.verified),
        verified=result.verified,
        chart=answer.chart,
        table=result,
        sql=result.sql,
    )


def frame_badge(locale: str, verified: bool) -> str:
    """Verified / unverified label in the answer language."""
    if verified:
        return "Verified" if locale == "en" else "Tasdiqlangan"
    return "Unverified" if locale == "en" else "Tasdiqlanmagan"


class AskAgent:
    """One dataset, one session: router → compiler, else the v2 LLM path."""

    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        model: DatasetModel,
        gateway: LLMGateway,
        settings: Settings,
        metrics: list[Metric] | None = None,
        pack: str | None = None,
        demo_key: str | None = None,
        locale: str = "uz",
        currency: str = "UZS",
    ) -> None:
        self.con = con
        self.model = model
        self.gateway = gateway
        self.settings = settings
        self.pack = pack or demo_key
        self.demo_key = demo_key
        self.locale = locale
        self.currency = currency
        self.metrics = metrics or available_metrics(model, self.pack)
        self.primary = primary_metric(model, self.pack)
        self.catalog = Catalog.of(model, self.metrics, self.primary)
        self.nl = NL2SQLAgent(con, model, gateway, settings, self.metrics, demo_key)
        self.previous: Route | None = None

    def ask(self, question: str, locale: str | None = None, level: str = "simple") -> PlainAnswer:
        """Answer one question. Never raises for expected problems."""
        last = None
        for _stage, payload in self.iter_ask(question, locale, level):
            if payload is not None:
                last = payload
        return last or failed_answer("", locale or self.locale)

    def iter_ask(
        self, question: str, locale: str | None = None, level: str = "simple"
    ) -> Iterator[tuple[str, PlainAnswer | None]]:
        """SSE stages: understanding → computing → writing → done."""
        loc = locale or self.locale
        yield "understanding", None
        question = question.strip()
        if not question:
            yield (
                "done",
                clarify_answer(
                    Route(
                        intent="total",
                        language=loc,
                        clarify="Savol yozing." if loc != "en" else "Please type a question.",
                    ),
                    loc,
                ),
            )
            return
        if len(question) > MAX_QUESTION_CHARS:
            yield (
                "done",
                failed_answer(
                    f"Please keep the question under {MAX_QUESTION_CHARS} characters.", loc
                ),
            )
            return
        if refuse_reason(question):
            yield "done", refuse_answer(refuse_text(loc))
            return
        latest = latest_date(self.con, self.model.roles)
        route = route_question(question, self.catalog, latest)
        route = self._inherit(question, route, latest)
        llm_first = should_use_llm(route, question)
        if route.clarify and not route.confident and not llm_first:
            yield "done", clarify_answer(route, loc)
            return
        yield "computing", None
        if route.confident and not llm_first:
            sql = compile_sql(route, self.model.roles)
            if sql:
                try:
                    result = execute_query(
                        self.con, sql, self.settings, [route.metric.id] if route.metric else []
                    )
                    yield "writing", None
                    tips = followups(route, loc, self.catalog.dimensions)
                    answer = compose(route, result, loc, level, self.currency, tips)
                    self.previous = route
                    yield "done", answer
                    return
                except (ValidationError, UserFacingError):
                    pass
        yield "writing", None
        wrapped = from_nl(self.nl.ask(question), loc, level)
        yield "done", wrapped

    def _inherit(self, question: str, route: Route, latest) -> Route:
        """A short follow-up such as ``shu filial bo'yicha-chi?`` keeps the last metric."""
        if self.previous is None or (route.confident and not route.use_llm):
            return route
        key = route.question_key
        if "boyicha" in key or "chi" in key.split() or key.endswith("chi"):
            if route.metric is None:
                route.metric = self.previous.metric
            if route.period is None:
                route.period = self.previous.period
            if route.dimension and route.metric:
                route.intent = "by_dimension"
                route.confident = True
                route.clarify = None
                route.use_llm = False
        return route
