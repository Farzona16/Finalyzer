"""The offline provider (golden question lookup) and shared golden-question helpers.

``OfflineProvider`` is the last link of every provider chain (spec 3.3): it sends nothing
anywhere. Deterministic frames and the router run without it; it adds the stored SQL of the
golden demo questions. Cloud and local providers live in their own modules and are built by
``core.llm.registry``.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel

from core.llm.base import Capabilities, LLMResponse

__all__ = [
    "OFFLINE_MESSAGE",
    "GoldenQuestion",
    "LLMResponse",
    "OfflineProvider",
    "load_golden_questions",
    "normalize_question",
]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GOLDEN_PATH = PROJECT_ROOT / "data" / "demo" / "golden_questions.yaml"
OFFLINE_MESSAGE = (
    "Offline mode: I can only answer the built-in demo questions right now. "
    "To ask anything else, configure an AI provider (ANTHROPIC_API_KEY or XAI_API_KEY with "
    "model names, or a local model) and restart."
)


class GoldenQuestion(BaseModel):
    """A question with its known-good SQL, used by the offline mode and the evaluation."""

    id: str
    dataset: str
    question: str
    expected_sql: str


def normalize_question(text: str) -> str:
    """Lowercase, replace punctuation by spaces and collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower())).strip()


def load_golden_questions(path: Path = DEFAULT_GOLDEN_PATH) -> list[GoldenQuestion]:
    """Read the golden question file."""
    with open(path, encoding="utf-8") as handle:
        return [GoldenQuestion.model_validate(q) for q in yaml.safe_load(handle)["questions"]]


class OfflineProvider:
    """Answers only the golden demo questions, using their stored SQL. Sends nothing anywhere."""

    name = "offline"
    model = "offline"
    api_base = ""
    external = False
    capabilities = Capabilities()

    def __init__(self, questions: list[GoldenQuestion] | None = None) -> None:
        self._questions = questions if questions is not None else load_golden_questions()
        self._index = {(q.dataset, normalize_question(q.question)): q for q in self._questions}

    def lookup(self, question: str, dataset: str | None) -> GoldenQuestion | None:
        """Find a golden question by its normalized text within one demo dataset."""
        if dataset is None:
            return None
        return self._index.get((dataset, normalize_question(question)))

    def suggestions(self, dataset: str | None, count: int = 6) -> list[str]:
        """Suggested questions for a demo dataset (spread over the whole list)."""
        pool = [q.question for q in self._questions if q.dataset == dataset]
        if len(pool) <= count:
            return pool
        step = len(pool) / count
        return [pool[int(i * step)] for i in range(count)]
