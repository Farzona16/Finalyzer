"""Privacy counters (`/api/v1/privacy/...`): bytes that left the machine in this process."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.deps import get_services, require
from app.models import User
from app.schemas.privacy import PrivacyEventsOut, PrivacySummaryOut
from app.security.permissions import Permission
from app.services.datasets import Services
from app.services.engine import core_settings, make_gateway

router = APIRouter(prefix="/privacy", tags=["privacy"])


@router.get("/summary", response_model=PrivacySummaryOut)
def privacy_summary(
    user: User = Depends(require(Permission.READ)),
    services: Services = Depends(get_services),
) -> PrivacySummaryOut:
    """How much left the machine since this process started (0 in offline mode)."""
    del user
    audit = services.audit
    settings = core_settings(services.settings)
    status = make_gateway(services, None, settings=settings).status()
    providers = [
        {k: v for k, v in item.items() if k in ("name", "model", "state", "models_checked")}
        for item in status["providers"]
    ]
    return PrivacySummaryOut(
        bytes_sent=audit.total_bytes_sent,
        blocked_calls=audit.blocked_count,
        tokens_used=audit.tokens_used,
        event_count=len(audit.events),
        mode=settings.effective_llm_mode,
        privacy_level=settings.privacy_level,
        template_mode=status["template_mode"],
        kill_switch=status["kill_switch"],
        budgets=status["budgets"],
        providers=providers,
        ephemeral=services.settings.storage_mode != "persistent",
    )


@router.get("/events", response_model=PrivacyEventsOut)
def privacy_events(
    user: User = Depends(require(Permission.READ)),
    services: Services = Depends(get_services),
) -> PrivacyEventsOut:
    """The exact payloads of every attempted egress call (empty when none were sent)."""
    del user
    return PrivacyEventsOut(events=services.audit.events)
