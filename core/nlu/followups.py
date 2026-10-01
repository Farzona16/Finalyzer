"""Three deterministic follow-up questions from the last route (spec 7.6)."""

from __future__ import annotations

from core.nlu.router import Route

FOLLOW = {
    "uz": {
        "trend": "{metric} oylar bo\u02bbyicha qanday o\u02bbzgardi?",
        "last": "Oxirgi oyda {metric} qancha bo\u02bbldi?",
        "by": "{metric} {dimension} bo\u02bbyicha qancha?",
        "top": "Qaysi {dimension} {metric} bo\u02bbyicha eng yuqori?",
        "share": "{metric} ning {dimension} kesimidagi ulushi qancha?",
        "yoy": "{metric} o\u02bbtgan yilga nisbatan qanday o\u02bbzgardi?",
    },
    "en": {
        "trend": "How did {metric} change by month?",
        "last": "What was {metric} last month?",
        "by": "What is {metric} by {dimension}?",
        "top": "Which {dimension} is highest for {metric}?",
        "share": "What is the share of {metric} by {dimension}?",
        "yoy": "How did {metric} change compared with last year?",
    },
}


def followups(route: Route, locale: str, dimensions: list[str]) -> list[str]:
    """Up to three next questions that reuse the bound metric."""
    loc = "en" if locale == "en" else "uz"
    metric = ""
    if route.metric is not None:
        metric = (
            str((route.metric.i18n.get("uz") or {}).get("label") or route.metric.label)
            if loc == "uz"
            else route.metric.label
        )
    dimension = route.dimension or (dimensions[0] if dimensions else "")
    catalog = FOLLOW[loc]
    keys = (
        ["trend", "last", "by"]
        if route.intent in {"total", "last_month", "period_value", "win_rate", "pipeline"}
        else ["top", "share", "yoy"]
    )
    seen: list[str] = []
    for key in keys:
        text = catalog[key].format(metric=metric or catalog[key], dimension=dimension or "")
        if text not in seen and metric:
            seen.append(text)
        if len(seen) == 3:
            break
    return seen
