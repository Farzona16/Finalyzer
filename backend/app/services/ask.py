"""Ask v2 in the backend: router first, then the v2 LLM path, stages for SSE."""

from __future__ import annotations

import json
from collections.abc import Iterator

from app.errors import AppError
from app.models import Dataset
from app.services.datasets import Services, stored_model
from app.services.engine import core_settings, domain_pack
from core.llm.gateway import LLMGateway
from core.metrics.registry import load_pack
from core.nlu.ask import AskAgent
from core.nlu.plain_answer import PlainAnswer


def answer(services: Services, row: Dataset, question: str, locale: str, level: str) -> PlainAnswer:
    """One complete answer (the JSON POST)."""
    stored = stored_model(row)
    if stored.built is None:
        raise AppError("not_found")
    settings = core_settings(services.settings)
    gateway = LLMGateway(settings, services.audit)
    with services.handles.use(row.id, row.storage_name, stored) as handle:
        agent = AskAgent(
            handle.workspace.con,
            handle.model,
            gateway,
            settings,
            pack=domain_pack(row, stored),
            locale=locale,
        )
        return agent.ask(question, locale, level)


def stages(
    services: Services, row: Dataset, question: str, locale: str, level: str
) -> Iterator[tuple[str, PlainAnswer | None]]:
    """SSE stages of one question."""
    stored = stored_model(row)
    if stored.built is None:
        raise AppError("not_found")
    settings = core_settings(services.settings)
    gateway = LLMGateway(settings, services.audit)
    with services.handles.use(row.id, row.storage_name, stored) as handle:
        agent = AskAgent(
            handle.workspace.con,
            handle.model,
            gateway,
            settings,
            pack=domain_pack(row, stored),
            locale=locale,
        )
        yield from agent.iter_ask(question, locale, level)


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
