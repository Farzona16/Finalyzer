"""Administration endpoints (`/api/v1/admin/...`): users, policy, audit."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import BackendSettings
from app.deps import get_app_settings, get_db, get_passwords, require
from app.models import AuditRecord, User
from app.schemas.admin import (
    AuditItemOut,
    AuditPageOut,
    AuditVerifyOut,
    PolicyIn,
    PolicyOut,
    UserCreateIn,
    UserPatchIn,
)
from app.schemas.auth import UserOut
from app.security import audit_chain
from app.security.passwords import PasswordService
from app.security.permissions import Permission
from app.services import users

router = APIRouter(prefix="/admin", tags=["admin"])


def _policy_out(user: User, settings: BackendSettings) -> PolicyOut:
    """The workspace policy plus the privacy modes the server allows."""
    workspace = user.workspace
    return PolicyOut(
        privacy_mode=workspace.privacy_mode,  # type: ignore[arg-type]
        allowed_privacy_modes=list(settings.allowed_modes),  # type: ignore[arg-type]
        currency=workspace.currency,  # type: ignore[arg-type]
        language=workspace.language,  # type: ignore[arg-type]
        retention_days=workspace.retention_days,
    )


@router.get("/users", response_model=list[UserOut])
def list_users(
    actor: User = Depends(require(Permission.MANAGE_USERS)), db: Session = Depends(get_db)
) -> list[User]:
    """All users of the workspace."""
    return users.list_users(db, actor.workspace_id)


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    body: UserCreateIn,
    actor: User = Depends(require(Permission.MANAGE_USERS)),
    db: Session = Depends(get_db),
    passwords: PasswordService = Depends(get_passwords),
) -> User:
    """Create a user with an initial password."""
    return users.create_user(
        db,
        passwords,
        actor,
        email=body.email,
        display_name=body.display_name,
        role=body.role,
        password=body.password,
    )


@router.patch("/users/{user_id}", response_model=UserOut)
def patch_user(
    user_id: str,
    body: UserPatchIn,
    actor: User = Depends(require(Permission.MANAGE_USERS)),
    db: Session = Depends(get_db),
    passwords: PasswordService = Depends(get_passwords),
) -> User:
    """Change name, role, status or password of a user."""
    return users.update_user(
        db,
        passwords,
        actor,
        user_id,
        display_name=body.display_name,
        role=body.role,
        is_active=body.is_active,
        new_password=body.new_password,
    )


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: str,
    actor: User = Depends(require(Permission.MANAGE_USERS)),
    db: Session = Depends(get_db),
) -> Response:
    """Delete a user."""
    users.delete_user(db, actor, user_id)
    return Response(status_code=204)


@router.get("/policy", response_model=PolicyOut)
def get_policy(
    actor: User = Depends(require(Permission.MANAGE_POLICY)),
    settings: BackendSettings = Depends(get_app_settings),
) -> PolicyOut:
    """The workspace policy."""
    return _policy_out(actor, settings)


@router.put("/policy", response_model=PolicyOut)
def put_policy(
    body: PolicyIn,
    actor: User = Depends(require(Permission.MANAGE_POLICY)),
    db: Session = Depends(get_db),
    settings: BackendSettings = Depends(get_app_settings),
) -> PolicyOut:
    """Change the workspace policy."""
    users.update_policy(
        db,
        settings,
        actor,
        privacy_mode=body.privacy_mode,
        currency=body.currency,
        language=body.language,
        retention_days=body.retention_days,
    )
    return _policy_out(actor, settings)


@router.get("/audit", response_model=AuditPageOut)
def get_audit(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    actor: User = Depends(require(Permission.VIEW_AUDIT)),
    db: Session = Depends(get_db),
) -> AuditPageOut:
    """Audit records of the workspace, newest first."""
    chain = audit_chain.chain_for(actor.workspace_id)
    total = db.scalar(
        select(func.count()).select_from(AuditRecord).where(AuditRecord.chain_id == chain)
    )
    rows = db.scalars(
        select(AuditRecord)
        .where(AuditRecord.chain_id == chain)
        .order_by(AuditRecord.seq.desc())
        .limit(limit)
        .offset(offset)
    )
    items = [
        AuditItemOut(
            seq=row.seq,
            ts=row.ts,
            event=row.event,
            actor_user_id=row.actor_user_id,
            detail=json.loads(row.detail),
            request_id=row.request_id,
            prev_hash=row.prev_hash,
            hash=row.hash,
        )
        for row in rows
    ]
    return AuditPageOut(total=int(total or 0), items=items)


@router.get("/audit/verify", response_model=AuditVerifyOut)
def verify_audit(
    actor: User = Depends(require(Permission.VIEW_AUDIT)), db: Session = Depends(get_db)
) -> AuditVerifyOut:
    """Recompute the hash chain of the workspace."""
    result = audit_chain.verify_chain(db, audit_chain.chain_for(actor.workspace_id))
    return AuditVerifyOut(
        ok=result.ok,
        checked=result.checked,
        broken_at_seq=result.broken_at_seq,
        reason=result.reason,
    )
