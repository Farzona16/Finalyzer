"""Ask request body."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AskIn(BaseModel):
    """One question about a built dataset."""

    question: str = Field(min_length=1, max_length=500)
    lang: Literal["uz", "en"] | None = None
    level: Literal["simple", "expert", "simpler"] = "simple"


class SuggestedOut(BaseModel):
    """Starter questions from the domain pack."""

    questions: list[str]
