"""Resolve metrics, dimensions, category values and columns with rapidfuzz (threshold 85)."""

from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import fuzz

from core.dashboard.chartspecs import dimension_options
from core.models import ColumnInfo, DatasetModel, Metric
from core.nlu.normalize import match_key

CUTOFF = 85.0
CLOSE = 5.0
DIMENSION_HINTS = {
    "store": "store_name",
    "dokon": "store_name",
    "magazin": "store_name",
    "branch": "branch_name",
    "filial": "branch_name",
    "category": "category",
    "kategoriya": "category",
    "turkum": "category",
    "channel": "channel",
    "kanal": "channel",
    "region": "region",
    "viloyat": "region",
    "mintaqa": "region",
    "owner": "owner_id",
    "menejer": "owner_id",
    "egasi": "owner_id",
    "source": "source",
    "manba": "source",
    "product": "product",
    "mahsulot": "product",
    "tovar": "product",
    "stage": "stage",
    "bosqich": "stage",
    "status": "status",
    "holat": "status",
    "line": "line",
    "liniya": "line",
    "shift": "shift",
    "smena": "shift",
    "operator": "operator",
    "segment": "segment",
    "mijoz": "customer_id",
    "customer": "customer_id",
    "xaridor": "customer_id",
    "txn_type": "txn_type",
    "tur": "txn_type",
}


@dataclass
class Hit[T]:
    """One catalog entry and how well it matched the question."""

    value: T
    score: float
    key: str


@dataclass
class Catalog:
    """Everything the router may bind: metrics, dimensions, values, numeric columns."""

    metrics: list[Metric]
    dimensions: list[str]
    columns: list[ColumnInfo]
    roles: dict[str, str]
    categories: dict[str, list[str]] = field(default_factory=dict)
    primary: Metric | None = None

    @classmethod
    def of(cls, model: DatasetModel, metrics: list[Metric], primary: Metric | None) -> Catalog:
        """Build a catalog from the dataset model and the available metrics."""
        categories = {c.name: list(c.categories or []) for c in model.columns if c.categories}
        return cls(
            metrics, dimension_options(model), model.columns, model.roles, categories, primary
        )


def metric_keys(metric: Metric) -> list[str]:
    """Labels, synonyms and the id, in match-key form."""
    labels = [metric.id, metric.label, metric.label.replace("%", "")]
    uz = metric.i18n.get("uz") or {}
    for name in ("label", "short", "label_gen"):
        if uz.get(name):
            labels.append(str(uz[name]))
    labels.extend(str(s) for s in uz.get("synonyms") or [])
    return [match_key(x) for x in labels if x]


def score_key(question: str, label: str) -> float:
    """100 when the label is a whole phrase of the question, otherwise token-set ratio."""
    if not label:
        return 0.0
    if re_word(label, question):
        return 100.0
    return float(fuzz.token_set_ratio(question, label))


def re_word(label: str, question: str) -> bool:
    """True when ``label`` occurs as a whole word/phrase in ``question``."""
    padded = f" {question} "
    return f" {label} " in padded


def ranked(question: str, items: list[tuple[str, object]]) -> list[Hit]:
    """Hits at or above the cutoff, highest score first, one value kept."""
    best: dict[int, Hit] = {}
    for key, value in items:
        score = score_key(question, key)
        if score < CUTOFF:
            continue
        identity = id(value)
        current = best.get(identity)
        if current is None or score > current.score:
            best[identity] = Hit(value, score, key)
    return sorted(best.values(), key=lambda h: -h.score)


def pick(hits: list[Hit]) -> tuple[object | None, list[object]]:
    """The unique winner, or (None, close alternatives) when two scores are close.

    At the same score the longer matching key wins, so a full label beats a short synonym.
    """
    if not hits:
        return None, []
    hits = sorted(hits, key=lambda h: (-h.score, -len(h.key)))
    if len(hits) == 1:
        return hits[0].value, []
    if hits[0].value is hits[1].value or id(hits[0].value) == id(hits[1].value):
        same = [h for h in hits if h.value is hits[0].value or h.value == hits[0].value]
        rest = [h for h in hits if h not in same]
        if not rest or hits[0].score - rest[0].score >= CLOSE:
            return hits[0].value, []
    if hits[0].score - hits[1].score >= CLOSE:
        return hits[0].value, []
    if hits[0].score == hits[1].score and len(hits[0].key) > len(hits[1].key):
        return hits[0].value, []
    close = [h.value for h in hits if hits[0].score - h.score < CLOSE]
    unique: list[object] = []
    for item in close:
        if item not in unique:
            unique.append(item)
    if len(unique) == 1:
        return unique[0], []
    return None, unique


def resolve_metric(question: str, catalog: Catalog) -> tuple[Metric | None, list[Metric], bool]:
    """The metric named in the question. ``explicit`` is false when only the primary was used."""
    items = [(key, m) for m in catalog.metrics for key in metric_keys(m)]
    chosen, options = pick(ranked(question, items))
    if chosen is not None:
        return chosen, [], True  # type: ignore[return-value]
    if options:
        return None, options, False  # type: ignore[return-value]
    return catalog.primary, [], False


def resolve_dimension(question: str, catalog: Catalog) -> tuple[str | None, list[str]]:
    """A grouping column named in the question."""
    items: list[tuple[str, object]] = []
    for name in catalog.dimensions:
        items.append((match_key(name), name))
        items.append((match_key(name.replace("_", " ")), name))
    for hint, target in DIMENSION_HINTS.items():
        if target in catalog.dimensions or any(c.name == target for c in catalog.columns):
            actual = (
                target
                if target in catalog.dimensions
                else next((c.name for c in catalog.columns if c.name == target), None)
            )
            if actual:
                items.append((hint, actual))
    chosen, options = pick(ranked(question, items))
    return chosen, options  # type: ignore[return-value]


def resolve_value(question: str, catalog: Catalog) -> tuple[tuple[str, str] | None, list[str]]:
    """A category value (and its column) mentioned in the question."""
    items: list[tuple[str, object]] = []
    for column, values in catalog.categories.items():
        for value in values:
            items.append((match_key(value), (column, value)))
    chosen, options = pick(ranked(question, items))
    if chosen is None:
        return None, [str(o[1]) if isinstance(o, tuple) else str(o) for o in options]
    column, value = chosen  # type: ignore[misc]
    return (str(column), str(value)), []


def numeric_columns(catalog: Catalog) -> list[str]:
    """Non-id numeric columns that statistics questions may name."""
    skip = set(catalog.roles.values())
    return [
        c.name for c in catalog.columns if c.dtype in ("integer", "float") and c.name not in skip
    ]


COLUMN_HINTS = {
    "harorat": "temperature",
    "temperature": "temperature",
    "bosim": "pressure",
    "pressure": "pressure",
    "qalinlik": "thickness_mm",
    "qalinlikning": "thickness_mm",
    "thickness": "thickness_mm",
    "defects": "defects",
    "nuqson": "defects",
}


def resolve_columns(question: str, catalog: Catalog, need: int = 1) -> list[str]:
    """Up to ``need`` numeric columns named in the question, best scores first."""
    names = numeric_columns(catalog)
    available = set(names)
    items = [(match_key(n.replace("_", " ")), n) for n in names]
    items += [(match_key(n), n) for n in names]
    for hint, target in COLUMN_HINTS.items():
        if target in available:
            items.append((hint, target))
    hits = ranked(question, items)
    found: list[str] = []
    for hit in hits:
        name = str(hit.value)
        if name not in found:
            found.append(name)
        if len(found) >= need:
            break
    return found
