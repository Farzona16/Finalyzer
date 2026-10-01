"""Domain classification (spec 6.1, step 1).

Every pack is scored from four kinds of evidence:

* **roles**: how many of the pack's core roles are mapped,
* **names**: column names (Uzbek, Russian, English) that the pack lists as hints,
* **values**: category values (``won``, ``completed``, ``paid``...) that the pack lists,
* **shape**: for ``statistics`` only: several numeric columns and no money column.

The best pack above ``THRESHOLD`` wins. Without one the data is ``statistics`` when it has
several numeric columns and no amount, else ``generic``. The result carries a confidence and
the evidence, so the interface can say *why* ("we think this is Sales data").
Offline, only these scores are used; an optional ``tiebreak`` function may settle a close race.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from core.autopilot.dsl import Context, is_id_like
from core.metrics.registry import MetricPack, list_pack_names, load_pack
from core.models import DatasetModel
from core.nlu.normalize import match_key

THRESHOLD = 0.45
CLOSE_RACE = 0.10
HINT_SATURATION = 3
MIN_NUMERIC_FOR_STATISTICS = 3
MIN_SUBSTRING_HINT = 5
WEIGHTS = {"roles": 0.40, "names": 0.25, "values": 0.25, "shape": 0.10}
STATISTICS_WEIGHTS = {"shape": 0.50, "names": 0.30, "values": 0.20}
WORD_RE = re.compile(r"[^\w]+|_")


@dataclass
class Evidence:
    """Why a pack scored as it did. ``items`` are role names, columns or values."""

    kind: str
    score: float
    items: list[str] = field(default_factory=list)


@dataclass
class Classification:
    """The decision for one dataset."""

    pack: str
    confidence: float
    scores: dict[str, float]
    evidence: list[Evidence]
    alternatives: list[str]
    source: str = "scores"  # "scores", "tiebreak" or "user"

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack": self.pack,
            "confidence": round(self.confidence, 3),
            "level": confidence_level(self.confidence),
            "source": self.source,
            "scores": {k: round(v, 3) for k, v in self.scores.items()},
            "alternatives": self.alternatives,
            "evidence": [
                {"kind": e.kind, "score": round(e.score, 3), "items": e.items}
                for e in self.evidence
            ],
        }


def confidence_level(confidence: float) -> str:
    """``high``, ``medium`` or ``low`` for display."""
    if confidence >= 0.7:
        return "high"
    return "medium" if confidence >= 0.5 else "low"


def name_tokens(name: str) -> tuple[str, set[str]]:
    """The normalized column name and its words."""
    key = match_key(name)
    return key, {t for t in WORD_RE.split(key) if t}


def hint_matches(hint: str, key: str, tokens: set[str]) -> bool:
    """A hint matches a whole word of the name, or (if long) any part of it."""
    wanted = match_key(hint)
    if wanted in tokens:
        return True
    return len(wanted) >= MIN_SUBSTRING_HINT and wanted in key.replace("_", " ")


def name_evidence(pack: MetricPack, model: DatasetModel) -> Evidence:
    """Column names that match the pack's hints."""
    found: list[str] = []
    hints_seen: set[str] = set()
    for column in model.columns:
        key, tokens = name_tokens(column.name)
        for hint in pack.column_hints:
            if hint_matches(hint, key, tokens):
                hints_seen.add(match_key(hint))
                if column.name not in found:
                    found.append(column.name)
    return Evidence("names", min(1.0, len(hints_seen) / HINT_SATURATION), found[:6])


def value_evidence(pack: MetricPack, model: DatasetModel) -> Evidence:
    """Category values that match the pack's hints."""
    wanted = {match_key(h): h for h in pack.value_hints}
    seen: dict[str, str] = {}
    for column in model.columns:
        for value in column.categories or []:
            key = match_key(str(value))
            if key in wanted:
                seen[key] = str(value)
    return Evidence("values", min(1.0, len(seen) / HINT_SATURATION), list(seen.values())[:6])


def role_evidence(pack: MetricPack, model: DatasetModel) -> Evidence:
    """Core roles of the pack that are mapped."""
    if not pack.roles_core:
        return Evidence("roles", 0.0)
    mapped = [r for r in pack.roles_core if r in model.roles]
    return Evidence("roles", len(mapped) / len(pack.roles_core), mapped)


def shape_evidence(pack: MetricPack, model: DatasetModel) -> Evidence:
    """Only ``statistics`` uses the shape: many numeric columns and no amount."""
    if pack.pack != "statistics":
        return Evidence("shape", 0.0)
    ctx = Context.from_model(model)
    numeric = [
        c.name
        for c in model.columns
        if c.dtype in ("integer", "float") and not c.is_pii and not is_id_like(c, ctx)
    ]
    ok = len(numeric) >= MIN_NUMERIC_FOR_STATISTICS and "amount" not in model.roles
    return Evidence("shape", 1.0 if ok else 0.0, numeric[:6] if ok else [])


def score_pack(pack: MetricPack, model: DatasetModel) -> tuple[float, list[Evidence]]:
    """Total score (0 to 1) and the evidence behind it."""
    parts = {
        "roles": role_evidence(pack, model),
        "names": name_evidence(pack, model),
        "values": value_evidence(pack, model),
        "shape": shape_evidence(pack, model),
    }
    weights = STATISTICS_WEIGHTS if pack.pack == "statistics" else WEIGHTS
    total = sum(weights.get(kind, 0.0) * ev.score for kind, ev in parts.items())
    return total, [ev for ev in parts.values() if ev.score > 0]


def classify(
    model: DatasetModel, tiebreak: Callable[[list[str]], str | None] | None = None
) -> Classification:
    """Pick the domain pack of a built dataset."""
    scored: dict[str, tuple[float, list[Evidence]]] = {}
    for name in list_pack_names():
        if name != "generic":
            scored[name] = score_pack(load_pack(name), model)
    ranked = sorted(scored, key=lambda n: (-scored[n][0], -load_pack(n).priority))
    scores = {name: scored[name][0] for name in ranked}
    best = ranked[0]
    if scores[best] < THRESHOLD:
        return fallback(model, scores, scored)
    close = [n for n in ranked if scores[best] - scores[n] <= CLOSE_RACE]
    source = "scores"
    if len(close) > 1 and tiebreak:
        choice = tiebreak(close)
        if choice in close:
            best, source = choice, "tiebreak"
    margin = scores[best] - max((s for n, s in scores.items() if n != best), default=0.0)
    confidence = min(1.0, scores[best]) * (0.6 + 0.4 * min(1.0, margin / 0.3))
    others = [n for n in ranked if n != best][:2]
    return Classification(best, confidence, scores, scored[best][1], others, source)


def fallback(
    model: DatasetModel, scores: dict[str, float], scored: dict[str, tuple[float, list[Evidence]]]
) -> Classification:
    """No pack is convincing: statistics for numeric tables, else generic."""
    shape = shape_evidence(load_pack("statistics"), model)
    ranked = [n for n in scores if n != "statistics"][:2]
    if shape.score > 0:
        confidence = 0.5 + 0.3 * min(1.0, scored["statistics"][0])
        return Classification("statistics", confidence, scores, scored["statistics"][1], ranked)
    return Classification("generic", 0.5, scores, [], ranked)
