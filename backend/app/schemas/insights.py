"""Insights cards and status (spec section 10)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.auth import StrictModel


class InsightCardOut(BaseModel):
    """One plain-language insight."""

    finding_id: str
    type: str
    direction: str
    level: str
    headline: str
    meaning: str
    next_step: str
    details: str
    evidence: list[str]
    priority: str
    confidence: str
    assessment: str
    polished: bool
    status: str = "open"


class NextStepsOut(BaseModel):
    """The three Next-steps lists."""

    missing: list[str] = Field(default_factory=list)
    track: list[str] = Field(default_factory=list)
    quality: list[str] = Field(default_factory=list)


class InsightsOut(BaseModel):
    """GET /datasets/{id}/insights."""

    cards: list[InsightCardOut]
    next_steps: NextStepsOut
    notes: list[str] = Field(default_factory=list)


class InsightStatusIn(StrictModel):
    """Mark a finding as done or dismissed."""

    status: Literal["open", "done", "dismissed"]
