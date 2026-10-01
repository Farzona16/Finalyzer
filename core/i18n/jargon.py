"""Jargon lint (spec 5.5): technical terms must not appear in Simple-level text.

A term is allowed only when it is wrapped as a glossary token ``[[key|visible text]]``; the
interface turns a token into a dotted-underline tooltip with the plain explanation.
"""

from __future__ import annotations

import re

from core.nlu.normalize import match_key

TOKEN_RE = re.compile(r"\[\[([a-z0-9_]+)\|([^\]]+)\]\]")

# Stems are compared on ``match_key`` form (no apostrophes, Latin letters), whole words only
# unless the entry ends with '*'.
BANNED = {
    "en": (
        "churn", "anomaly", "anomalies", "concentration", "correlation", "median",
        "standard deviation", "variance", "margin", "win rate", "p-value", "hhi",
        "z-score", "regression", "anova", "t-test", "outlier*", "iqr", "skewness",
        "quartile*", "percentile*", "cohort*", "cagr", "ebitda", "cardinality",
        "null*", "pearson", "spearman", "chi-square", "effect size", "confidence interval",
    ),
    "uz": (
        "churn", "anomaliya*", "konsentratsiya*", "korrelyatsiya*", "mediana*",
        "standart ogish*", "dispersiya*", "marja", "p-qiymat*", "hhi",
        "z-ball*", "regressiya*", "anova", "t-test", "outlier*", "iqr", "assimetriya*",
        "kvartil*", "persentil*", "kogorta*", "cagr", "ebitda", "kardinallik*",
        "null*", "pirson", "spirmen", "xi-kvadrat*", "ishonch oraligi*",
    ),
}


def _patterns(locale: str) -> list[tuple[str, re.Pattern[str]]]:
    result = []
    for term in BANNED[locale]:
        stem = term.endswith("*")
        key = match_key(term.rstrip("*"))
        body = re.escape(key) + (r"\w*" if stem else "")
        result.append((term.rstrip("*"), re.compile(rf"(?<!\w){body}(?!\w)")))
    return result


_COMPILED = {locale: _patterns(locale) for locale in BANNED}


def strip_tokens(text: str) -> str:
    """Remove glossary tokens (their content is allowed to be technical)."""
    return TOKEN_RE.sub(" ", text)


def find_jargon(text: str, locale: str) -> list[str]:
    """Banned terms that occur outside glossary tokens, in order of the banned list."""
    if locale not in BANNED:
        raise ValueError(f"unsupported locale: {locale!r}")
    haystack = match_key(strip_tokens(text))
    return [term for term, pattern in _COMPILED[locale] if pattern.search(haystack)]
