"""AI-suggested metrics and domain tie-break (spec 6.1, steps 1 and 3).

The model sees the schema and the profile only (category values follow the privacy mode) and
answers with metric objects in the restricted language of ``dsl.py``. It never writes SQL; the
objects are checked and compiled by code. Any failure (no key, blocked payload, bad JSON) gives
an empty list: the offline heuristics still work.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from core.config import Settings
from core.i18n.glossary import find_entry, load_glossary
from core.llm.gateway import LLMGateway
from core.models import DatasetModel
from core.query.anonymizer import IdentifierMask
from core.query.prompts import describe_column

logger = logging.getLogger("finalyzer.autopilot")

MAX_SUGGESTIONS = 8
PURPOSE_SUGGEST = "autopilot_suggest"
PURPOSE_DOMAIN = "autopilot_domain"

SYSTEM_PROMPT = f"""You design business metrics for a table called `data`.
Reply with one JSON object: {{"metrics": [ ... ]}} with at most {MAX_SUGGESTIONS} metrics.
Never write SQL. Describe each metric with this language only:
{{"id": "snake_case_id", "agg": "<agg>", "columns": ["<column>"],
 "filter": [{{"column": "<column>", "op": "<op>", "value": <value>}}],
 "unit": "currency|count|percent|ratio", "good_direction": "up|down|neutral",
 "label": {{"en": "English name", "uz": "Uzbek name"}}}}
agg is one of: sum, avg, count, count_distinct, min, max, median, stddev, quantile (with "q"),
avg_days_between (two date columns), ratio (with "numerator" and "denominator" metric objects
and optional "times100": true).
op is one of: =, !=, in, >, >=, <, <=, is_null, not_null. For = and in use only values that are
listed for the column. Use only the columns listed. Do not use identifier columns in sums or
averages. Prefer metrics a business owner would watch. Uzbek labels use the letters o\u02bb and
g\u02bb and no technical words."""


class SuggestReply(BaseModel):
    """What the model must return."""

    metrics: list[dict[str, Any]] = Field(default_factory=list)


class DomainReply(BaseModel):
    """Answer of the domain tie-break."""

    pack: str = ""


def schema_text(model: DatasetModel, settings: Settings) -> str:
    """The columns of the dataset as the model may see them in this privacy mode."""
    return "\n".join(describe_column(c, settings) for c in model.columns if not c.is_pii)


def suggest_messages(
    model: DatasetModel,
    pack_id: str,
    known_ids: list[str],
    settings: Settings,
    mask: IdentifierMask | None = None,
) -> list[dict[str, str]]:
    """Chat messages of the suggestion request (identifiers already masked)."""
    mask = mask or IdentifierMask.from_model(model)
    schema = mask.mask_text(schema_text(model, settings))
    user = (
        f"Business type guess: {pack_id}\n"
        f"Rows: {model.row_count}\n"
        f"Columns of `{mask.table}`:\n{schema}\n\n"
        f"Metrics that already exist (do not repeat them): {', '.join(known_ids) or 'none'}\n"
        "Suggest the extra metrics now."
    )
    return [
        {"role": "system", "content": mask.mask_text(SYSTEM_PROMPT)},
        {"role": "user", "content": user},
    ]


def suggest_specs(
    gateway: LLMGateway | None, model: DatasetModel, pack_id: str, known_ids: list[str]
) -> list[dict[str, Any]]:
    """Raw metric objects proposed by the model (unchecked), or ``[]`` when none can be had."""
    if gateway is None or gateway.is_offline:
        return []
    mask = IdentifierMask.from_model(model)
    messages = suggest_messages(model, pack_id, known_ids, gateway.settings, mask)
    try:
        reply = gateway.complete_json(PURPOSE_SUGGEST, messages, SuggestReply)
    except Exception as exc:  # noqa: BLE001 - the model is optional; never fail the build
        logger.info("autopilot_suggest_skipped type=%s", type(exc).__name__)
        return []
    return [mask.unmask_spec(item) for item in reply.metrics[:MAX_SUGGESTIONS]]


def make_tiebreak(
    gateway: LLMGateway | None, model: DatasetModel
) -> Callable[[list[str]], str | None] | None:
    """A function that asks the model to choose between close domains (None when offline)."""
    if gateway is None or gateway.is_offline:
        return None

    def tiebreak(candidates: list[str]) -> str | None:
        mask = IdentifierMask.from_model(model)
        schema = mask.mask_text(schema_text(model, gateway.settings))
        user = (
            f"Which business type fits this table best: {', '.join(candidates)}?\n"
            f"Columns:\n{schema}\n"
            'Reply with {"pack": "<one of the options>"}.'
        )
        messages = [
            {"role": "system", "content": "You classify tables by business type. JSON only."},
            {"role": "user", "content": user},
        ]
        try:
            return gateway.complete_json(PURPOSE_DOMAIN, messages, DomainReply).pack or None
        except Exception as exc:  # noqa: BLE001 - the model is optional
            logger.info("autopilot_domain_skipped type=%s", type(exc).__name__)
            return None

    return tiebreak


def glossary_labels(label_en: str) -> dict[str, str] | None:
    """Reviewed wording for a term the glossary knows (spec 6.1, step 5), in both languages."""
    entry = find_entry(label_en, "en")
    if entry is None:
        return None
    uz = load_glossary("uz").get(entry.key)
    return {"en": entry.term, "uz": uz.term[:1].upper() + uz.term[1:]} if uz else None


def finalize_labels(labels: dict[str, str]) -> tuple[dict[str, str], bool]:
    """Labels to show and whether the Uzbek one is machine-made (``ai_translated``)."""
    english = labels.get("en", "")
    known = glossary_labels(english) if english else None
    if known:
        return {**labels, "uz": known["uz"]}, False
    if labels.get("uz"):
        return labels, True
    return {**labels, "uz": english}, True
