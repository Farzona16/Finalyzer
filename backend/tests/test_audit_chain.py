"""S11: the audit log is append-only evidence; tampering is detected."""

from __future__ import annotations

import json

from app.db import session_scope
from app.models import AuditRecord
from app.models.audit import GENESIS_HASH
from app.security import audit_chain
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app import clock
from tests.conftest import ADMIN_EMAIL, add_user, first_admin, login, new_session, with_csrf


def workspace_chain(client: TestClient) -> str:
    me = client.get("/api/v1/auth/me").json()
    return audit_chain.chain_for(me["workspace"]["id"])


def rows(client: TestClient, chain: str) -> list[AuditRecord]:
    with session_scope(client.app.state.session_factory) as db:
        return list(
            db.scalars(
                select(AuditRecord).where(AuditRecord.chain_id == chain).order_by(AuditRecord.seq)
            )
        )


def verify(client: TestClient, chain: str) -> audit_chain.VerifyResult:
    with session_scope(client.app.state.session_factory) as db:
        return audit_chain.verify_chain(db, chain)


def test_the_chain_starts_at_genesis_and_links_every_record(client: TestClient) -> None:
    first_admin(client)
    add_user(client, "viewer@example.com")
    chain = workspace_chain(client)
    records = rows(client, chain)
    assert [r.seq for r in records] == list(range(1, len(records) + 1))
    assert len(records) >= 2
    assert records[0].prev_hash == GENESIS_HASH
    for before, after in zip(records, records[1:], strict=False):
        assert after.prev_hash == before.hash
    assert verify(client, chain) == audit_chain.VerifyResult(True, len(records))
    assert client.get("/api/v1/admin/audit/verify").json()["ok"] is True


def test_changing_a_detail_is_detected(client: TestClient) -> None:
    first_admin(client)
    add_user(client, "viewer@example.com")
    chain = workspace_chain(client)
    with session_scope(client.app.state.session_factory) as db:
        record = db.scalars(
            select(AuditRecord).where(AuditRecord.chain_id == chain, AuditRecord.seq == 2)
        ).one()
        record.detail = json.dumps({"target": "someone-else", "role": "viewer"})
    result = verify(client, chain)
    assert (result.ok, result.broken_at_seq, result.reason) == (False, 2, "record changed")
    api = client.get("/api/v1/admin/audit/verify").json()
    assert api["ok"] is False
    assert api["broken_at_seq"] == 2


def test_deleting_a_record_in_the_middle_is_detected(client: TestClient) -> None:
    first_admin(client)
    add_user(client, "a@example.com")
    add_user(client, "b@example.com")
    chain = workspace_chain(client)
    with session_scope(client.app.state.session_factory) as db:
        db.execute(text("delete from audit_chain where chain_id = :c and seq = 2"), {"c": chain})
    result = verify(client, chain)
    assert result.ok is False
    assert result.reason == "sequence gap"


def test_rewriting_the_event_name_is_detected(client: TestClient) -> None:
    first_admin(client)
    chain = workspace_chain(client)
    with session_scope(client.app.state.session_factory) as db:
        record = db.scalars(select(AuditRecord).where(AuditRecord.chain_id == chain)).first()
        record.event = "something_harmless"
    assert verify(client, chain).ok is False


def test_a_failed_login_for_an_unknown_email_goes_to_the_system_chain(
    client: TestClient,
) -> None:
    first_admin(client)
    attacker = with_csrf(new_session(client))
    attacker.post("/api/v1/auth/login", json={"email": "ghost@example.com", "password": "x" * 14})
    system = rows(client, audit_chain.SYSTEM_CHAIN)
    assert [r.event for r in system] == ["login_failed"]
    assert verify(client, audit_chain.SYSTEM_CHAIN).ok is True
    # The workspace chain does not mention it.
    assert "ghost" not in "".join(r.detail for r in rows(client, workspace_chain(client)))


def test_events_never_contain_secrets_or_e_mail_addresses(client: TestClient) -> None:
    first_admin(client)
    add_user(client, "viewer@example.com")
    attacker = with_csrf(new_session(client))
    attacker.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong-password-1"})
    login(new_session(client), "viewer@example.com")
    with session_scope(client.app.state.session_factory) as db:
        text_dump = " ".join(
            f"{r.event} {r.detail} {r.actor_user_id}" for r in db.scalars(select(AuditRecord))
        )
    for forbidden in ("wrong-password-1", "Quiet-River-Stone-42", "@example.com", "password"):
        assert forbidden not in text_dump, forbidden


def test_expected_events_are_recorded(client: TestClient) -> None:
    first_admin(client)
    user = add_user(client, "viewer@example.com")
    client.patch(f"/api/v1/admin/users/{user['id']}", json={"role": "analyst"})
    client.delete(f"/api/v1/admin/users/{user['id']}")
    client.post("/api/v1/auth/logout")
    with session_scope(client.app.state.session_factory) as db:
        events = {r.event for r in db.scalars(select(AuditRecord))}
    assert {
        "first_admin_created",
        "user_created",
        "role_change",
        "user_deleted",
        "logout",
    } <= events


def test_each_record_keeps_the_request_id_and_a_time(client: TestClient, monkeypatch) -> None:
    from datetime import timedelta

    start = clock.now()
    first_admin(client)
    monkeypatch.setattr(clock, "now", lambda: start + timedelta(minutes=10))
    add_user(client, "viewer@example.com")
    records = rows(client, workspace_chain(client))
    assert records[-1].ts - records[0].ts >= timedelta(minutes=9)
    assert all(len(r.request_id) >= 8 for r in records)


def test_the_audit_api_pages_newest_first(client: TestClient) -> None:
    first_admin(client)
    for index in range(3):
        add_user(client, f"user{index}@example.com")
    page = client.get("/api/v1/admin/audit?limit=2").json()
    assert page["total"] >= 4
    assert [i["seq"] for i in page["items"]] == sorted(
        (i["seq"] for i in page["items"]), reverse=True
    )
    assert len(page["items"]) == 2
    assert client.get("/api/v1/admin/audit?limit=500").status_code == 422
