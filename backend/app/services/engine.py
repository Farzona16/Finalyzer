"""The bridge to the v2 engine in ``core/``: its settings, built from the backend settings."""

from __future__ import annotations

from app.config import REPO_ROOT, BackendSettings
from app.models import Dataset
from app.schemas.datasets import StoredModel
from core.config import Settings as CoreSettings


def core_settings(settings: BackendSettings, privacy_mode: str | None = None) -> CoreSettings:
    """Engine settings. Limits come from the backend; LLM keys come from the environment.

    Tests never read a ``.env`` file and never go online.
    """
    values: dict[str, object] = {
        "privacy_mode": privacy_mode or settings.privacy_mode,
        "max_file_mb": settings.max_upload_mb,
        "max_rows": settings.max_rows,
        "duckdb_memory_limit": settings.duckdb_memory_limit,
    }
    if settings.app_env == "test":
        return CoreSettings(_env_file=None, llm_mode="offline", **values)  # type: ignore[arg-type]
    return CoreSettings(_env_file=REPO_ROOT / ".env", **values)  # type: ignore[arg-type]


def domain_pack(row: Dataset, stored: StoredModel) -> str | None:
    """The domain pack to use: a user override, then the autopilot guess."""
    if stored.domain_source == "user" and row.domain:
        return row.domain
    domain = (stored.autopilot or {}).get("domain") or {}
    return domain.get("pack") or row.domain
