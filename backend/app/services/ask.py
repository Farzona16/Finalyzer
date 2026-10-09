"""Ask v2 in the backend: router first, then the v2 LLM path, stages for SSE."""

from __future__ import annotations

import json
from collections.abc import Iterator

from app.errors import AppError
from app.models import Dataset
from app.services.datasets import Services, stored_model
from app.services.engine import core_settings, domain_pack, make_gateway
from core.metrics.registry import load_pack
from core.nlu.ask import AskAgent
from core.nlu.plain_answer import PlainAnswer


def without_sql(answer: PlainAnswer) -> PlainAnswer:
    """Viewers do not see generated SQL (D12 / SEE_SQL). Reasoning text stays."""
    table = answer.table.model_copy(update={"sql": ""}) if answer.table is not None else None
    details = answer.details
    if answer.sql:
        details = details.replace(answer.sql, "").strip()
    return answer.model_copy(update={"sql": None, "table": table, "details": details})


def answer(
    services: Services,
    row: Dataset,
    question: str,
    locale: str,
    level: str,
    show_sql: bool = True,
) -> PlainAnswer:
    """One complete answer (the JSON POST)."""
    stored = stored_model(row)
    if stored.built is None:
        raise AppError("not_found")
    settings = core_settings(services.settings)
    gateway = make_gateway(services, row, stored, settings)
    with services.handles.use(row.id, row.storage_name, stored) as handle:
        agent = AskAgent(
            handle.workspace.con,
            handle.model,
            gateway,
            settings,
            pack=domain_pack(row, stored),
            locale=locale,
        )
        payload = agent.ask(question, locale, level)
    return payload if show_sql else without_sql(payload)


def stages(
    services: Services,
    row: Dataset,
    question: str,
    locale: str,
    level: str,
    show_sql: bool = True,
) -> Iterator[tuple[str, PlainAnswer | None]]:
    """SSE stages of one question."""
    stored = stored_model(row)
    if stored.built is None:
        raise AppError("not_found")
    settings = core_settings(services.settings)
    gateway = make_gateway(services, row, stored, settings)
    with services.handles.use(row.id, row.storage_name, stored) as handle:
        agent = AskAgent(
            handle.workspace.con,
            handle.model,
            gateway,
            settings,
            pack=domain_pack(row, stored),
            locale=locale,
        )
        for stage, payload in agent.iter_ask(question, locale, level):
            if payload is not None and not show_sql:
                payload = without_sql(payload)
            yield stage, payload


def suggestions(services: Services, row: Dataset, locale: str) -> list[str]:
    """Starter questions from the pack (or the generic pack)."""
    stored = stored_model(row)
    name = domain_pack(row, stored) or "generic"
    try:
        pack = load_pack(name)
    except FileNotFoundError:
        pack = load_pack("generic")
    questions = pack.suggested_questions.get(locale) or pack.suggested_questions.get("uz") or []
    return list(questions)


def sse_line(stage: str, payload: PlainAnswer | None) -> str:
    """One Server-Sent Event."""
    body = {"stage": stage, "answer": payload.model_dump(mode="json") if payload else None}
    return f"event: {stage}\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"
