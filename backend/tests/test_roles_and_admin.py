"""S6: the role matrix, admin protections and workspace isolation."""

from __future__ import annotations

import pytest
from app.db import session_scope
from app.errors import AppError
from app.models import User
from app.security.permissions import ROLE_PERMISSIONS, Permission, Role, has_permission
from app.services import users
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.conftest import (
    ADMIN_EMAIL,
    STRONG_PASSWORD,
    add_user,
    first_admin,
    insert_workspace_with_admin,
    login,
    new_session,
    with_csrf,
)


def signed_in(client: TestClient, email: str) -> TestClient:
    """A fresh browser signed in as ``email``."""
    browser = new_session(client)
    assert login(browser, email) == 200
    return browser


@pytest.fixture
def team(client: TestClient) -> dict[str, TestClient]:
    """An app with an admin, an analyst and a viewer, each in an own browser."""
    first_admin(client)
    add_user(client, "analyst@example.com", "analyst")
    add_user(client, "viewer@example.com", "viewer")
    return {
        "admin": client,
        "analyst": signed_in(client, "analyst@example.com"),
        "viewer": signed_in(client, "viewer@example.com"),
    }


def test_the_matrix_matches_the_spec() -> None:
    assert ROLE_PERMISSIONS[Role.VIEWER] == {Permission.READ}
    assert ROLE_PERMISSIONS[Role.ANALYST] == {
        Permission.READ,
        Permission.UPLOAD,
        Permission.EDIT_KPIS,
        Permission.SEE_SQL,
    }
    assert ROLE_PERMISSIONS[Role.ADMIN] == set(Permission)
    assert not has_permission("superuser", Permission.READ)
    assert not has_permission("", Permission.READ)


@pytest.mark.parametrize("role", ["analyst", "viewer"])
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/admin/users"),
        ("post", "/api/v1/admin/users"),
        ("get", "/api/v1/admin/policy"),
        ("put", "/api/v1/admin/policy"),
        ("get", "/api/v1/admin/audit"),
        ("get", "/api/v1/admin/audit/verify"),
    ],
)
def test_only_admins_reach_the_admin_area(team, role: str, method: str, path: str) -> None:
    response = getattr(team[role], method)(
        path, **({"json": {}} if method in {"post", "put"} else {})
    )
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_admin_endpoints_work_for_admins(team) -> None:
    admin = team["admin"]
    users = admin.get("/api/v1/admin/users")
    assert users.status_code == 200
    assert {u["email"] for u in users.json()} == {
        ADMIN_EMAIL,
        "analyst@example.com",
        "viewer@example.com",
    }
    assert admin.get("/api/v1/admin/policy").status_code == 200
    assert admin.get("/api/v1/admin/audit/verify").json()["ok"] is True


def test_me_lists_the_permissions_of_the_role(team) -> None:
    permissions = team["viewer"].get("/api/v1/auth/me").json()["permissions"]
    assert permissions == ["read"]


def test_an_admin_may_demote_another_admin_while_two_are_active(client: TestClient) -> None:
    first_admin(client)
    second = add_user(client, "second@example.com", "admin")
    response = client.patch(f"/api/v1/admin/users/{second['id']}", json={"role": "viewer"})
    assert response.status_code == 200
    assert response.json()["role"] == "viewer"


def test_nobody_can_delete_or_deactivate_themselves(client: TestClient) -> None:
    first_admin(client)
    me = client.get("/api/v1/auth/me").json()["user"]["id"]
    assert client.delete(f"/api/v1/admin/users/{me}").json()["code"] == "cannot_modify_self"
    response = client.patch(f"/api/v1/admin/users/{me}", json={"is_active": False})
    assert response.json()["code"] == "cannot_modify_self"


def test_the_service_refuses_to_remove_the_last_active_admin(client: TestClient) -> None:
    """Defence in depth: the route cannot reach this state (sessions end on deactivation),
    but the service must still hold if it ever does."""
    first_admin(client)
    second = add_user(client, "second@example.com", "admin")
    app = client.app
    with session_scope(app.state.session_factory) as db:
        actor = db.scalar(select(User).where(User.email == ADMIN_EMAIL))
        actor.is_active = False  # a stale actor object: no longer an active admin
        db.commit()
        target = db.get(User, second["id"])
        with pytest.raises(AppError) as demote:
            users.update_user(
                db,
                app.state.passwords,
                actor,
                target.id,
                display_name=None,
                role="viewer",
                is_active=None,
                new_password=None,
            )
        assert demote.value.code == "last_admin"
        with pytest.raises(AppError) as delete:
            users.delete_user(db, actor, target.id)
        assert delete.value.code == "last_admin"


def test_an_admin_cannot_change_their_own_role_or_password_here(client: TestClient) -> None:
    first_admin(client)
    me = client.get("/api/v1/auth/me").json()["user"]["id"]
    for payload in (
        {"role": "viewer"},
        {"is_active": False},
        {"new_password": STRONG_PASSWORD + "x"},
    ):
        response = client.patch(f"/api/v1/admin/users/{me}", json=payload)
        assert response.json()["code"] == "cannot_modify_self"
    # Renaming yourself is fine.
    assert (
        client.patch(f"/api/v1/admin/users/{me}", json={"display_name": "New Name"}).status_code
        == 200
    )


def test_role_change_ends_the_sessions_of_that_user(team) -> None:
    admin, analyst = team["admin"], team["analyst"]
    analyst_id = analyst.get("/api/v1/auth/me").json()["user"]["id"]
    assert (
        admin.patch(f"/api/v1/admin/users/{analyst_id}", json={"role": "viewer"}).status_code == 200
    )
    assert analyst.get("/api/v1/auth/me").status_code == 401


def test_deactivating_a_user_ends_their_sessions(team) -> None:
    admin, viewer = team["admin"], team["viewer"]
    viewer_id = viewer.get("/api/v1/auth/me").json()["user"]["id"]
    assert (
        admin.patch(f"/api/v1/admin/users/{viewer_id}", json={"is_active": False}).status_code
        == 200
    )
    assert viewer.get("/api/v1/auth/me").status_code == 401
    assert login(new_session(admin), "viewer@example.com") == 401


def test_admin_set_password_is_checked_by_the_policy(team) -> None:
    admin, viewer = team["admin"], team["viewer"]
    viewer_id = viewer.get("/api/v1/auth/me").json()["user"]["id"]
    response = admin.patch(f"/api/v1/admin/users/{viewer_id}", json={"new_password": "short"})
    assert response.status_code == 422
    good = admin.patch(
        f"/api/v1/admin/users/{viewer_id}", json={"new_password": "a-brand-new-passphrase-1"}
    )
    assert good.status_code == 200
    assert login(new_session(admin), "viewer@example.com", "a-brand-new-passphrase-1") == 200


def test_duplicate_email_is_refused_case_insensitively(team) -> None:
    response = team["admin"].post(
        "/api/v1/admin/users",
        json={
            "email": "ANALYST@example.com",
            "display_name": "Dup",
            "role": "viewer",
            "password": STRONG_PASSWORD,
        },
    )
    assert response.json()["code"] == "email_taken"


def test_unknown_roles_are_rejected(team) -> None:
    response = team["admin"].post(
        "/api/v1/admin/users",
        json={
            "email": "x@example.com",
            "display_name": "X",
            "role": "root",
            "password": STRONG_PASSWORD,
        },
    )
    assert response.status_code == 422


def test_mass_assignment_is_rejected(team) -> None:
    """Extra fields (a workspace id, a password hash) are refused, not silently used."""
    admin = team["admin"]
    response = admin.post(
        "/api/v1/admin/users",
        json={
            "email": "x@example.com",
            "display_name": "X",
            "role": "viewer",
            "password": STRONG_PASSWORD,
            "workspace_id": "other",
            "password_hash": "x",
        },
    )
    assert response.status_code == 422
    me = team["viewer"].patch("/api/v1/auth/me", json={"role": "admin"})
    assert me.status_code == 422


def test_other_workspaces_look_like_they_do_not_exist(client: TestClient) -> None:
    first_admin(client)
    foreign_id = insert_workspace_with_admin(client, "boss@other.example")
    assert foreign_id not in {u["id"] for u in client.get("/api/v1/admin/users").json()}
    for call in (
        client.patch(f"/api/v1/admin/users/{foreign_id}", json={"role": "viewer"}),
        client.delete(f"/api/v1/admin/users/{foreign_id}"),
    ):
        assert call.status_code == 404
        assert call.json()["code"] == "not_found"


def test_audit_log_shows_only_the_own_workspace(client: TestClient) -> None:
    first_admin(client)
    insert_workspace_with_admin(client, "boss@other.example")
    items = client.get("/api/v1/admin/audit").json()["items"]
    assert items
    assert all("other" not in str(item["detail"]) for item in items)


def test_policy_privacy_mode_must_be_allowed_by_the_server(make_client) -> None:
    client = make_client(allowed_privacy_modes="demo,local")
    first_admin(client)
    policy = client.get("/api/v1/admin/policy").json()
    assert policy["allowed_privacy_modes"] == ["demo", "local"]
    refused = client.put(
        "/api/v1/admin/policy",
        json={"privacy_mode": "cloud", "currency": "UZS", "language": "uz", "retention_days": None},
    )
    assert refused.json()["code"] == "privacy_mode_not_allowed"
    changed = client.put(
        "/api/v1/admin/policy",
        json={"privacy_mode": "local", "currency": "USD", "language": "en", "retention_days": 30},
    )
    assert changed.status_code == 200
    assert changed.json()["currency"] == "USD"
    events = [i["event"] for i in client.get("/api/v1/admin/audit").json()["items"]]
    assert "privacy_mode_change" in events


def test_retention_days_are_bounded(client: TestClient) -> None:
    first_admin(client)
    for days in (0, 3651, -5):
        response = client.put(
            "/api/v1/admin/policy",
            json={
                "privacy_mode": "demo",
                "currency": "UZS",
                "language": "uz",
                "retention_days": days,
            },
        )
        assert response.status_code == 422
    assert with_csrf(client) is client
