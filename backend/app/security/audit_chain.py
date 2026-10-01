"""Tamper-evident audit log (S11): every record stores the SHA-256 of the previous one.

There is one chain per workspace plus one ``system`` chain for events that belong to no
workspace (for example a failed login for an unknown e-mail). Detail dictionaries may only
hold ids, counters and short codes: never passwords, tokens, payloads or data values.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import clock
from app.logging_setup import request_id_var
from app.models import AuditRecord
from app.models.audit import GENESIS_HASH

SYSTEM_CHAIN = "system"
_append_lock = threading.Lock()

# Event names used across the backend (the spec lists the first group).
LOGIN = "login"
LOGIN_FAILED = "login_failed"
UPLOAD = "upload"
DELETE = "delete"
ROLE_CHANGE = "role_change"
PRIVACY_MODE_CHANGE = "privacy_mode_change"
EGRESS_CALL = "egress_call"
EXPORT = "export"
LOGOUT = "logout"
FIRST_ADMIN = "first_admin_created"
PASSWORD_CHANGE = "password_change"
USER_CREATED = "user_created"
USER_UPDATED = "user_updated"
USER_DELETED = "user_deleted"
REFRESH_REUSE = "refresh_reuse_detected"
POLICY_CHANGE = "policy_change"


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of walking a chain."""

    ok: bool
    checked: int
    broken_at_seq: int | None = None
    reason: str = ""


def chain_for(workspace_id: str | None) -> str:
    """Chain id of a workspace, or the system chain."""
    return f"ws:{workspace_id}" if workspace_id else SYSTEM_CHAIN


def canonical_detail(detail: dict[str, object]) -> str:
    """Stable JSON text of the detail dictionary."""
    return json.dumps(detail, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def compute_hash(prev_hash: str, record: AuditRecord) -> str:
    """Hash of a record's fields together with the previous hash."""
    parts = [
        prev_hash,
        str(record.seq),
        record.ts.astimezone(UTC).isoformat(timespec="microseconds"),
        record.chain_id,
        record.workspace_id or "",
        record.actor_user_id or "",
        record.event,
        record.detail,
        record.request_id,
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def record_event(
    db: Session,
    event: str,
    *,
    workspace_id: str | None = None,
    actor_user_id: str | None = None,
    detail: dict[str, object] | None = None,
) -> AuditRecord:
    """Append one event and commit.

    Pending changes of the session are committed first, so this thread holds no database
    write lock while it waits for the append lock (that would deadlock two requests).
    """
    db.commit()
    with _append_lock:
        chain_id = chain_for(workspace_id)
        last = db.scalars(
            select(AuditRecord)
            .where(AuditRecord.chain_id == chain_id)
            .order_by(AuditRecord.seq.desc())
            .limit(1)
        ).first()
        record = AuditRecord(
            chain_id=chain_id,
            seq=(last.seq + 1) if last else 1,
            ts=clock.now(),
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            event=event,
            detail=canonical_detail(detail or {}),
            request_id=request_id_var.get(),
            prev_hash=last.hash if last else GENESIS_HASH,
            hash="",
        )
        record.hash = compute_hash(record.prev_hash, record)
        db.add(record)
        db.commit()
    return record


def verify_chain(db: Session, chain_id: str) -> VerifyResult:
    """Recompute every hash of a chain and check linkage and numbering."""
    records = db.scalars(
        select(AuditRecord).where(AuditRecord.chain_id == chain_id).order_by(AuditRecord.seq)
    ).all()
    expected_prev = GENESIS_HASH
    for position, record in enumerate(records, start=1):
        if record.seq != position:
            return VerifyResult(False, position - 1, record.seq, "sequence gap")
        if record.prev_hash != expected_prev:
            return VerifyResult(False, position - 1, record.seq, "broken link")
        if compute_hash(record.prev_hash, record) != record.hash:
            return VerifyResult(False, position - 1, record.seq, "record changed")
        expected_prev = record.hash
    return VerifyResult(True, len(records))


def as_utc_text(value: datetime) -> str:
    """ISO text of a timestamp in UTC (used by API responses)."""
    return value.astimezone(UTC).isoformat(timespec="seconds")
