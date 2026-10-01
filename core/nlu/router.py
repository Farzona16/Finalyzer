"""Intent router: ≥ 30 parametrized patterns, then slot filling (spec 7.2)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.models import Metric
from core.nlu.entities import (
    Catalog,
    resolve_columns,
    resolve_dimension,
    resolve_metric,
    resolve_value,
)
from core.nlu.language import detect_language
from core.nlu.normalize import match_key, normalize_text
from core.nlu.periods import Period, parse_period

TOP_DEFAULT = 5


@dataclass(frozen=True)
class Intent:
    """One parametrized question type."""

    name: str
    patterns: tuple[str, ...]
    required: tuple[str, ...]
    priority: int


@dataclass
class Route:
    """A filled intent, or a clarification / 'not confident' signal for the LLM path."""

    intent: str
    language: str
    metric: Metric | None = None
    dimension: str | None = None
    n: int | None = None
    period: Period | None = None
    value: tuple[str, str] | None = None
    column: str | None = None
    column_b: str | None = None
    options: list[str] = field(default_factory=list)
    clarify: str | None = None
    confident: bool = False
    question_key: str = ""


# Higher priority wins. Patterns run on match_key (no apostrophes).
INTENTS: tuple[Intent, ...] = (  # noqa: E501
    Intent(
        "correlation",
        (r"\b(bogliq\w*|korrelyatsiya|correlation|birga ozgar)\b",),
        ("columns2",),
        100,
    ),
    Intent("funnel", (r"\b(voronka|funnel|konversiya|conversion by stage|bosqichlar)\b",), (), 95),
    Intent("stalled", (r"\b(toxtab|stalled|eski ochiq|open deals older)\b",), (), 95),
    Intent(
        "why_drop",
        (r"\bnega\b.{0,80}\b(tushdi|kamaydi|drop)|why did .{0,40}(drop|fall|decline)|\bsababi\b",),
        ("metric",),
        90,
    ),
    Intent("yoy", (r"\b(otgan yilga|year.over.year|\byoy\b|yilga nisbatan)\b",), ("metric",), 88),
    Intent("mom", (r"\b(oyma.?oy|month.over.month|\bmom\b|oyga nisbatan)\b",), ("metric",), 86),
    Intent("share", (r"\b(ulushi|hissasi|share of|percentage of|foizi)\b",), ("metric",), 84),
    Intent(
        "top_n",
        (r"\b(eng kop|eng yuqori|top \d|\d ta |highest|eng yaxshi \d)\b",),
        ("metric", "dimension"),
        82,
    ),
    Intent(
        "bottom_n",
        (r"\b(eng kam|eng past|bottom \d|lowest|eng yomon)\b",),
        ("metric", "dimension"),
        80,
    ),
    Intent(
        "best",
        (r"\b(qaysi \w+ eng (yaxshi|yuqori)|which \w+ (best|highest)|eng yaxshi natija)\b",),
        ("metric", "dimension"),
        78,
    ),
    Intent(
        "worst",
        (r"\b(qaysi \w+ eng (yomon|past)|which \w+ (worst|lowest))\b",),
        ("metric", "dimension"),
        76,
    ),
    Intent(
        "trend",
        (r"\b(oylar boyicha|over time|dinamika|trend|oyma oy qanday|by month)\b",),
        ("metric",),
        74,
    ),
    Intent("daily", (r"\b(kunlik|per day|by day|daily)\b",), ("metric",), 72),
    Intent(
        "having_gt",
        (r"\b(dan kop|more than|greater than|kamida \d)\b",),
        ("metric", "dimension", "n"),
        70,
    ),
    Intent("median", (r"\b(median|ortadagi qiymat\w*)\b",), ("column",), 68),
    Intent(
        "distribution",
        (r"\b(taqsimot|distribution|histogram|qanday tarqalgan)\b",),
        ("column",),
        66,
    ),
    Intent("missing", (r"\b(yetishmayotgan|missing|bush qiymat|null)\b",), ("column",), 64),
    Intent("minimum", (r"\b(minimum|minimal|eng kichik qiymat|lowest value)\b",), ("column",), 62),
    Intent(
        "maximum", (r"\b(maksimum|maksimal|eng katta qiymat|highest value)\b",), ("column",), 60
    ),
    Intent("average", (r"\b(ortacha|average|mean)\b",), (), 58),
    Intent("distinct", (r"\b(nechta turli|distinct|unique|turli)\b",), ("dimension",), 56),
    Intent("count", (r"\b(nechta|how many|qatorlar soni|row count)\b",), (), 54),
    Intent("last_month", (r"\b(oxirgi oy(?:da)?|last month|otgan oy(?:da)?)\b",), ("metric",), 85),
    Intent("this_year", (r"\b(bu yil|this year)\b",), ("metric",), 50),
    Intent(
        "period_value",
        (
            r"\b(yanvar|fevral|mart|aprel|iyun|iyul|avgust|sentabr|oktabr|noyabr|dekabr|january|february|march|april|june|july|august|september|october|november|december|20\d{2}-\d{2})\b",
        ),
        ("metric", "period"),
        48,
    ),
    Intent("rank", (r"\b(orin|rank|reyting)\b",), ("metric", "dimension"), 46),
    Intent(
        "list_values", (r"\b(qanday qiymatlar|which values|royxat|list of)\b",), ("dimension",), 44
    ),
    Intent("filter_value", (r"\bda\b|\bfor\b|uchun qancha",), ("metric", "value"), 42),
    Intent(
        "by_dimension", (r"\b(boyicha|kesimida|\bby \b|grouped by)\b",), ("metric", "dimension"), 40
    ),
    Intent("compare_segments", (r"\b(va \w+ orasida|compared with|qarshi)\b",), ("metric",), 38),
    Intent("growth", (r"\b(osish|growth|ozgarish foizi)\b",), ("metric",), 36),
    Intent("ratio", (r"\b(nisbati|ratio of)\b",), ("columns2",), 34),
    Intent("win_rate", (r"\b(yutish ulushi|win rate)\b",), ("metric",), 93),
    Intent("pipeline", (r"\b(ochiq bitim|pipeline value|voronka qiymat)\b",), ("metric",), 91),
    Intent("total", (r"\b(jami|total|umumiy|qancha|how much)\b",), ("metric",), 20),
)

COMPILED = [
    (intent, tuple(re.compile(p) for p in intent.patterns))
    for intent in sorted(INTENTS, key=lambda i: -i.priority)
]
N_RE = re.compile(r"\b(\d{1,2})\b")


def parse_n(key: str) -> int | None:
    """A small integer that is not a year."""
    for match in N_RE.finditer(key):
        value = int(match.group(1))
        if value not in {20} and not (2000 <= value <= 2100) and 1 <= value <= 50:
            return value
    return None


def first_intent(key: str) -> Intent | None:
    """Highest-priority intent whose pattern occurs in the question."""
    for intent, patterns in COMPILED:
        if any(p.search(key) for p in patterns):
            return intent
    return None


def _clarify(locale: str, kind: str, options: list[str]) -> tuple[str, list[str]]:
    if locale == "en":
        prompts = {
            "metric": "Which figure should I use?",
            "dimension": "Split by which group?",
            "column": "Which column?",
        }
    else:
        prompts = {
            "metric": "Qaysi ko\u02bbrsatkichni olay?",
            "dimension": "Qaysi guruh bo\u02bbyicha bo\u02bbay?",
            "column": "Qaysi ustunni olay?",
        }
    return prompts.get(kind, prompts["metric"]), options[:4]


def fill(intent: Intent, question: str, catalog: Catalog, latest) -> Route:
    """Bind slots of ``intent``. ``confident`` is true only when every required slot is unique."""
    key = match_key(normalize_text(question))
    locale = detect_language(question)
    period = parse_period(question, latest)
    n = parse_n(key)
    metric, metric_opts, _explicit = resolve_metric(key, catalog)
    dimension, dim_opts = resolve_dimension(key, catalog)
    value, _ = resolve_value(key, catalog)
    columns = resolve_columns(key, catalog, 2)
    route = Route(
        intent=intent.name,
        language=locale,
        metric=metric,
        dimension=dimension,
        n=n,
        period=period,
        value=value,
        column=columns[0] if columns else None,
        column_b=columns[1] if len(columns) > 1 else None,
        question_key=key,
    )
    if intent.name in {"top_n", "bottom_n"} and route.n is None:
        route.n = TOP_DEFAULT
    if intent.name in {"best", "worst"}:
        route.n = 1
    if intent.name == "why_drop" and not route.dimension and catalog.dimensions:
        route.dimension = catalog.dimensions[0]
    if intent.name == "last_month" and route.period is None and latest is not None:
        from core.nlu.periods import last_month_of

        start, end = last_month_of(latest)
        route.period = Period("last_month", start, end, f"{start.year:04d}-{start.month:02d}")
    missing = []
    for slot in intent.required:
        if slot == "metric" and route.metric is None:
            missing.append(("metric", [m.label for m in metric_opts or catalog.metrics[:4]]))
        elif slot == "dimension" and not route.dimension:
            missing.append(("dimension", dim_opts or catalog.dimensions[:4]))
        elif slot == "period" and route.period is None:
            missing.append(("period", []))
        elif slot == "n" and route.n is None:
            missing.append(("n", ["5", "10"]))
        elif slot == "value" and route.value is None:
            missing.append(("value", []))
        elif slot == "column" and not route.column:
            missing.append(("column", columns or catalog.dimensions[:4]))
        elif slot == "columns2" and (not route.column or not route.column_b):
            missing.append(("column", columns))
    if missing:
        kind, options = missing[0]
        route.clarify, route.options = _clarify(
            locale if locale != "ru" else "uz", kind, [str(o) for o in options]
        )
        return route
    if route.intent == "total" and route.period is not None:
        route.intent = "period_value"
    if route.value is not None and route.intent in {"total", "period_value"}:
        route.intent = "filter_value"
    route.confident = True
    return route


def route_question(question: str, catalog: Catalog, latest=None) -> Route:
    """The best intent for ``question``. Unknown wording becomes a clarification, not a guess."""
    key = match_key(normalize_text(question))
    locale = detect_language(question)
    intent = first_intent(key)
    if intent is None:
        prompt, options = _clarify(
            "en" if locale == "en" else "uz",
            "metric",
            [m.label for m in catalog.metrics[:4]],
        )
        return Route(
            intent="total", language=locale, clarify=prompt, options=options, question_key=key
        )
    return fill(intent, question, catalog, latest)
