"""The bridge to the v2 engine in ``core/``: its settings, built from the backend settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config import REPO_ROOT, BackendSettings
from app.models import Dataset
from app.schemas.datasets import StoredModel
from core.config import Settings as CoreSettings
from core.llm.gateway import LLMGateway
from core.runtime.profile import load_limits

if TYPE_CHECKING:
    from app.services.datasets import Services


def core_settings(settings: BackendSettings, privacy_mode: str | None = None) -> CoreSettings:
    """Engine settings. Limits come from the backend; LLM keys come from the environment.

    Tests never read a ``.env`` file and never go online.
    """
    limits = load_limits(getattr(settings, "resource_profile", "") or None)
    values: dict[str, object] = {
        "privacy_mode": privacy_mode or settings.privacy_mode,
        "max_file_mb": min(settings.max_upload_mb, limits.max_file_mb),
        "max_rows": min(settings.max_rows, limits.max_rows),
        "duckdb_memory_limit": settings.duckdb_memory_limit or limits.duckdb_max_memory,
        "sql_candidates": limits.ask_candidates,
        "ask_candidates": limits.ask_candidates,
        "session_token_budget": limits.session_token_budget,
        "dataset_token_budget": limits.dataset_token_budget,
        "daily_token_budget": limits.daily_token_budget,
        "insight_precompute": limits.insight_precompute,
    }
    if settings.app_env == "test":
        return CoreSettings(_env_file=None, llm_mode="offline", **values)  # type: ignore[arg-type]
    return CoreSettings(_env_file=REPO_ROOT / ".env", **values)  # type: ignore[arg-type]


def make_gateway(
    services: Services, row: Dataset | None, stored: StoredModel | None = None,
    settings: CoreSettings | None = None,
) -> LLMGateway:
    """A gateway on the shared provider runtime, scoped to one dataset's origin and budget."""
    settings = settings or core_settings(services.settings)
    origin = stored.origin if stored is not None else "real"
    return LLMGateway(
        settings,
        services.audit,
        runtime=services.llm_runtime,
        data_origin=origin,  # type: ignore[arg-type]
        dataset_id=row.id if row is not None else None,
        kill_switch=services.kill_switch,
    )


def domain_pack(row: Dataset, stored: StoredModel) -> str | None:
    """The domain pack to use: a user override, then the autopilot guess."""
    if stored.domain_source == "user" and row.domain:
        return row.domain
    domain = (stored.autopilot or {}).get("domain") or {}
    return domain.get("pack") or row.domain
