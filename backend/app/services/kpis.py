"""KPI autopilot in the backend: run it during a build and serve the KPI manager.

The autopilot result is stored inside the dataset's ``model_json`` (``StoredModel.autopilot``).
What the user changes in the KPI manager is stored next to it (``kpi_prefs``), so a rebuild
never loses the choices of a person and the engine result stays untouched.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.errors import AppError
from app.i18n import translate
from app.models import Dataset
from app.schemas.datasets import (
    DomainOut,
    KpiListOut,
    KpiOut,
    KpiPatchIn,
    KpiPref,
    StoredModel,
)
from app.services.datasets import Services, stored_model
from app.services.engine import core_settings
from core.autopilot.run import run_autopilot
from core.dashboard.kpi import KpiValue, compute_kpis, selected_range
from core.errors import UserFacingError
from core.i18n.formats import (
    MINUS,
    format_compact,
    format_currency,
    format_decimal,
    format_integer,
    format_percent,
)
from core.llm.gateway import LLMGateway
from core.metrics.builder import metric_sql
from core.metrics.registry import load_pack
from core.models import DatasetModel, FilterSpec, Metric

logger = logging.getLogger("finalyzer.kpis")
LANGUAGES = ("uz", "en")
MAX_DASHBOARD_TILES = 8
COMPACT_FROM = 10_000
REASON_KINDS = ("roles", "names", "values", "shape")
MIN_PERIOD_DAYS = 7
MAX_PERIOD_DAYS = 90
LATE = 10_000  # sort position of metrics without an order


# ---------------------------------------------------------------------- the build step


def run_for_build(
    services: Services, row: Dataset, stored: StoredModel, model: DatasetModel, con: Any
) -> None:
    """Run the autopilot on an open dataset and keep its result in ``stored``."""
    settings = core_settings(services.settings)
    override = row.domain if stored.domain_source == "user" else None
    result = run_autopilot(model, con, settings, LLMGateway(settings, services.audit), override)
    stored.autopilot = result.to_dict()
    if stored.domain_source != "user":
        row.domain = result.classification.pack
    logger.info(
        "autopilot_done pack=%s metrics=%d tiles=%d dropped=%d seconds=%.2f",
        result.classification.pack,
        len(result.metrics),
        len(result.tiles),
        len(result.dropped),
        result.seconds,
    )


# ---------------------------------------------------------------------- reading


def pick_language(lang: str | None) -> str:
    """``uz`` or ``en`` (anything else becomes Uzbek, the default language)."""
    return lang if lang in LANGUAGES else "uz"


def metrics_of(stored: StoredModel) -> list[Metric]:
    """The accepted metrics of the autopilot, best first."""
    if not stored.autopilot:
        return []
    return [Metric.model_validate(m) for m in stored.autopilot.get("metrics", [])]


def is_enabled(metric_id: str, stored: StoredModel) -> bool:
    """A KPI is shown when the user turned it on, or the autopilot chose it and it is not off."""
    pref = stored.kpi_prefs.get(metric_id)
    if pref and pref.enabled is not None:
        return pref.enabled
    return metric_id in (stored.autopilot or {}).get("tiles", [])


def order_key(metric_id: str, position: int, stored: StoredModel) -> tuple[int, int]:
    """Sort key: the user's order, else the autopilot's tile order, else the score order."""
    pref = stored.kpi_prefs.get(metric_id)
    if pref and pref.order is not None:
        return (pref.order, position)
    tiles = (stored.autopilot or {}).get("tiles", [])
    if metric_id in tiles:
        return (tiles.index(metric_id), position)
    return (LATE, position)


def tile_ids(stored: StoredModel) -> list[str]:
    """The ids of the KPIs on the dashboard, in display order."""
    metrics = metrics_of(stored)
    chosen = [(m.id, i) for i, m in enumerate(metrics) if is_enabled(m.id, stored)]
    chosen.sort(key=lambda item: order_key(item[0], item[1], stored))
    return [metric_id for metric_id, _ in chosen][:MAX_DASHBOARD_TILES]


def label_of(metric: Metric, lang: str, pref: KpiPref | None) -> str:
    """The name to show: the user's rename, else the translated label."""
    if pref and pref.label.get(lang):
        return pref.label[lang]
    if lang == "uz":
        return str(metric.i18n.get("uz", {}).get("label") or metric.label)
    return metric.label


def format_value(value: float | None, unit: str, lang: str, currency: str) -> str:
    """A number in the shared Uzbek or English style."""
    if value is None:
        return "—"
    if unit == "percent":
        return format_percent(value, lang)
    if unit == "currency":
        return format_currency(value, lang, currency)
    if unit == "ratio":
        return format_decimal(value, lang, 2)
    whole = float(value).is_integer()
    if abs(value) >= COMPACT_FROM:
        return format_compact(value, lang)
    return format_integer(value, lang) if whole else format_decimal(value, lang, 1)


def format_delta(delta: float | None, kind: str | None, lang: str) -> str:
    """``+12,3%`` or ``-1,5 pp``; empty when there is nothing to compare with."""
    if delta is None or kind is None:
        return ""
    sign = "+" if delta > 0 else (MINUS if delta < 0 else "")
    magnitude = abs(delta)
    if kind == "pct":
        return f"{sign}{format_percent(magnitude, lang)}"
    return f"{sign}{format_decimal(magnitude, lang, 1)} pp"


def default_period(con: Any, model: DatasetModel) -> tuple[date, date] | None:
    """The period the KPI tiles describe: the last stretch of the data (at most 90 days).

    The stretch is half of the data span (7 to 90 days), so that an equally long period
    before it exists and the tiles can show a change against it.
    """
    span = selected_range(con, model, FilterSpec())
    if span is None:
        return None
    low, high = span
    days = (high - low).days + 1
    length = max(MIN_PERIOD_DAYS, min(MAX_PERIOD_DAYS, days // 2))
    if days < 2 * MIN_PERIOD_DAYS:
        return None  # too short to compare two periods; show everything
    return (high - timedelta(days=length - 1), high)


def tile_values(
    services: Services, row: Dataset, stored: StoredModel, wanted: list[Metric]
) -> tuple[dict[str, KpiValue], tuple[date, date] | None]:
    """Current value and change against the previous period for the tile metrics."""
    if not wanted or stored.built is None:
        return {}, None
    settings = core_settings(services.settings)
    try:
        with services.handles.use(row.id, row.storage_name, stored) as handle:
            con = handle.workspace.con
            period = default_period(con, handle.model)
            spec = FilterSpec(date_range=period) if period else FilterSpec()
            cards = compute_kpis(
                con, handle.model, wanted, spec, settings, limit=MAX_DASHBOARD_TILES
            )
    except UserFacingError:
        return {}, None
    return {card.metric_id: card for card in cards}, period


def domain_view(stored: StoredModel, lang: str) -> DomainOut | None:
    """The detected domain, with the reasons in plain words."""
    info = (stored.autopilot or {}).get("domain")
    if not info:
        return None
    pack = load_pack(info["pack"])
    name = pack.labels.get(lang, info["pack"])
    reasons = []
    for evidence in sorted(info.get("evidence", []), key=lambda e: -e["score"]):
        if evidence["kind"] in REASON_KINDS:
            items = ", ".join(f"«{item}»" for item in evidence["items"][:4])
            reasons.append(translate(lang, f"autopilot.reason.{evidence['kind']}", items=items))
    alternatives = [
        {"pack": alt, "label": load_pack(alt).labels.get(lang, alt)}
        for alt in info.get("alternatives", [])
    ]
    return DomainOut(
        pack=info["pack"],
        label=name,
        headline=translate(lang, "autopilot.guess", domain=name),
        confidence=info["confidence"],
        level=info["level"],
        source=info["source"],
        reasons=reasons,
        alternatives=alternatives,
    )


def expert_view(metric: Metric, model: DatasetModel) -> dict[str, Any]:
    """The definition, SQL and DSL of a KPI (analyst and admin only)."""
    return {
        "definition": metric.expr,
        "sql": f"SELECT {metric_sql(metric, model.roles)} AS value FROM data",
        "dsl": metric.spec,
    }


def kpi_item(
    metric: Metric,
    stored: StoredModel,
    lang: str,
    currency: str,
    card: KpiValue | None,
    expert: dict[str, Any] | None,
) -> KpiOut:
    """One row of the KPI list."""
    pref = stored.kpi_prefs.get(metric.id)
    uz = metric.i18n.get("uz", {})
    value = card.value if card else (stored.autopilot or {}).get("values", {}).get(metric.id)
    label = label_of(metric, lang, pref)
    description = uz.get("description_plain") if lang == "uz" else metric.description
    return KpiOut(
        id=metric.id,
        label=label,
        short=str(uz.get("short") or label) if lang == "uz" else label,
        description=str(description or label),
        unit=metric.unit,
        good_direction=metric.good_direction,
        source=metric.source,
        ai_translated=metric.ai_translated and lang == "uz",
        enabled=is_enabled(metric.id, stored),
        is_tile=metric.id in tile_ids(stored),
        order=pref.order if pref else None,
        value=value,
        display=format_value(value, metric.unit, lang, currency),
        previous=card.previous if card else None,
        delta=card.delta if card else None,
        delta_kind=card.delta_kind if card else None,
        delta_display=format_delta(card.delta, card.delta_kind, lang) if card else "",
        tone=card.tone if card else "neutral",
        expert=expert,
    )


def kpi_list(
    services: Services, row: Dataset, lang: str, currency: str, *, show_expert: bool
) -> KpiListOut:
    """Everything the KPI manager and the dashboard's KPI row need."""
    stored = stored_model(row)
    metrics = metrics_of(stored)
    tiles = tile_ids(stored)
    by_id = {m.id: m for m in metrics}
    cards, period = tile_values(services, row, stored, [by_id[t] for t in tiles])
    built = stored.built
    items = [
        kpi_item(
            m,
            stored,
            lang,
            currency,
            cards.get(m.id),
            expert_view(m, built) if show_expert and built else None,
        )
        for m in metrics
    ]
    items.sort(key=lambda i: (i.id not in tiles, tiles.index(i.id) if i.id in tiles else 0))
    return KpiListOut(
        domain=domain_view(stored, lang),
        tiles=tiles,
        items=items,
        period={"start": period[0].isoformat(), "end": period[1].isoformat()} if period else None,
    )


# ---------------------------------------------------------------------- editing


def patch_kpi(db: Session, row: Dataset, metric_id: str, patch: KpiPatchIn) -> None:
    """Apply one KPI manager edit."""
    stored = stored_model(row)
    if metric_id not in {m.id for m in metrics_of(stored)}:
        raise AppError("not_found")
    pref = stored.kpi_prefs.get(metric_id) or KpiPref()
    if patch.enabled is not None:
        pref.enabled = patch.enabled
    if patch.label is not None:
        pref.label = {**pref.label, **{k: v for k, v in patch.label.items()}}
    if patch.order is not None:
        pref.order = patch.order
    stored.kpi_prefs[metric_id] = pref
    row.model_json = stored.model_dump_json()
    db.commit()
