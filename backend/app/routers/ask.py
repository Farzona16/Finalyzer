"""Ask (`/api/v1/datasets/{id}/ask`): router SQL first, LLM path as fallback, SSE stages."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.deps import dataset_of, get_services, limit
from app.errors import locale_of
from app.models import Dataset
from app.schemas.ask import AskIn, SuggestedOut
from app.security.permissions import Permission
from app.services import ask as service
from app.services.datasets import Services
from core.nlu.plain_answer import PlainAnswer

router = APIRouter(prefix="/datasets", tags=["ask"])
Lang = Literal["uz", "en"]
ask_limit = limit("ask")


@router.post("/{dataset_id}/ask", response_model=PlainAnswer)
def ask_question(
    body: AskIn,
    request: Request,
    dataset: Dataset = Depends(dataset_of(Permission.READ)),
    services: Services = Depends(get_services),
    _: None = Depends(ask_limit),
) -> PlainAnswer:
    """Answer one question. The router path is verified; the LLM path may not be."""
    locale = body.lang or locale_of(request)
    return service.answer(services, dataset, body.question, locale, body.level)


def _events(
    services: Services, dataset: Dataset, question: str, locale: str, level: str
) -> Iterator[str]:
    for stage, payload in service.stages(services, dataset, question, locale, level):
        yield service.sse_line(stage, payload)


@router.get("/{dataset_id}/ask/stream")
def ask_stream(
    request: Request,
    question: str = Query(default="", max_length=500),
    lang: Lang | None = Query(default=None),
    level: Literal["simple", "expert", "simpler"] = Query(default="simple"),
    dataset: Dataset = Depends(dataset_of(Permission.READ)),
    services: Services = Depends(get_services),
    _: None = Depends(ask_limit),
) -> StreamingResponse:
    """The same answer as SSE stages ``understanding`` → ``computing`` → ``writing`` → ``done``."""
    locale = lang or locale_of(request)
    return StreamingResponse(
        _events(services, dataset, question, locale, level),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{dataset_id}/suggested-questions", response_model=SuggestedOut)
def suggested_questions(
    request: Request,
    lang: Lang | None = Query(default=None),
    dataset: Dataset = Depends(dataset_of(Permission.READ)),
    services: Services = Depends(get_services),
) -> SuggestedOut:
    """Pack questions the Ask box can offer as one-click starters."""
    return SuggestedOut(
        questions=service.suggestions(services, dataset, lang or locale_of(request))
    )
