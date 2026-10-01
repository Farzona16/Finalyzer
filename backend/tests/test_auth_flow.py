"""U2 / S1-S4: sign-in, first run, refresh rotation, reuse detection, password change, logout."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import clock
from tests.conftest import (
    ADMIN_EMAIL,
    STRONG_PASSWORD,
    add_user,
    first_admin,
    login,
    new_session,
    with_csrf,
)


def test_first_run_creates_workspace_admin_and_a_session(client: TestClient) -> None:
    body = first_admin(client)
    assert body["user"]["role"] == "admin"
    assert body["user"]["email"] == ADMIN_EMAIL
    assert body["workspace"]["currency"] == "UZS"
    assert "manage_users" in body["permissions"]
    assert "password_hash" not in str(body)
    assert client.get("/api/v1/auth/me").json()["user"]["email"] == ADMIN_EMAIL


def test_first_run_works_only_once(client: TestClient) -> None:
    first_admin(client)
    other = with_csrf(new_session(client))
    response = other.post(
        "/api/v1/auth/first-admin",
        json={
            "email": "evil@example.com",
            "password": STRONG_PASSWORD,
            "display_name": "Evil",
            "workspace_name": "Evil Corp",
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "first_admin_exists"


def test_login_success_sets_cookies_with_the_right_attributes(client: TestClient) -> None:
    first_admin(client)
    other = with_csrf(new_session(client))
    response = other.post(
        "/api/v1/auth/login", json={"email": ADMIN_EMAIL.upper(), "password": STRONG_PASSWORD}
    )
    assert response.status_code == 200
    cookies = {h.split("=")[0]: h for h in response.headers.get_list("set-cookie")}
    access, refresh = cookies["fz_access"], cookies["fz_refresh"]
    for header in (access, refresh):
        assert "HttpOnly" in header
        assert "SameSite=lax" in header
    assert "Path=/api/v1;" in access or "Path=/api/v1" in access
    assert "Path=/api/v1/auth" in refresh


def test_cookies_are_secure_when_configured(make_client) -> None:
    secure_client = make_client(cookie_secure=True)
    secure_client.base_url = "https://testserver"  # type: ignore[assignment]
    with_csrf(secure_client)
    response = secure_client.post(
        "/api/v1/auth/first-admin",
        json={
            "email": ADMIN_EMAIL,
            "password": STRONG_PASSWORD,
            "display_name": "A",
            "workspace_name": "W",
        },
    )
    assert response.status_code == 201
    assert all("Secure" in h for h in response.headers.get_list("set-cookie"))


def test_wrong_password_and_unknown_user_look_identical(client: TestClient) -> None:
    first_admin(client)
    other = with_csrf(new_session(client))

    def attempt(email: str) -> tuple[int, dict]:
        response = other.post(
            "/api/v1/auth/login", json={"email": email, "password": "wrong-password-1"}
        )
        body = response.json()
        body.pop("correlation_id")
        return response.status_code, body

    assert attempt(ADMIN_EMAIL) == attempt("nobody@example.com")
    assert attempt(ADMIN_EMAIL)[0] == 401


def test_inactive_user_cannot_sign_in(client: TestClient) -> None:
    first_admin(client)
    created = add_user(client, "worker@example.com", "analyst")
    client.patch(f"/api/v1/admin/users/{created['id']}", json={"is_active": False})
    assert login(new_session(client), "worker@example.com") == 401


def test_refresh_rotates_and_the_old_token_stops_working(client: TestClient) -> None:
    first_admin(client)
    old_refresh = client.cookies.get("fz_refresh", path="/api/v1/auth")
    assert old_refresh
    response = client.post("/api/v1/auth/refresh")
    assert response.status_code == 200
    new_refresh = client.cookies.get("fz_refresh", path="/api/v1/auth")
    assert new_refresh and new_refresh != old_refresh
    replay = new_session(client)
    replay.cookies.set("fz_refresh", old_refresh, path="/api/v1/auth")
    with_csrf(replay)
    assert replay.post("/api/v1/auth/refresh").status_code == 401


def test_reusing_a_refresh_token_revokes_the_whole_family(client: TestClient) -> None:
    first_admin(client)
    stolen = client.cookies.get("fz_refresh", path="/api/v1/auth")
    assert client.post("/api/v1/auth/refresh").status_code == 200  # the legitimate rotation
    latest = client.cookies.get("fz_refresh", path="/api/v1/auth")
    thief = with_csrf(new_session(client))
    thief.cookies.set("fz_refresh", stolen, path="/api/v1/auth")
    assert thief.post("/api/v1/auth/refresh").status_code == 401  # reuse detected
    # The legitimate user's newest token and access token are now dead too.
    assert client.post("/api/v1/auth/refresh").status_code == 401
    assert latest
    assert client.get("/api/v1/auth/me").status_code == 401


def test_refresh_without_a_cookie_is_rejected(client: TestClient) -> None:
    with_csrf(client)
    response = client.post("/api/v1/auth/refresh")
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"


def test_expired_refresh_token_is_rejected(client: TestClient, monkeypatch) -> None:
    first_admin(client)
    later = clock.now() + timedelta(days=8)
    monkeypatch.setattr(clock, "now", lambda: later)
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_access_token_expires_after_fifteen_minutes(client: TestClient, monkeypatch) -> None:
    first_admin(client)
    assert client.get("/api/v1/auth/me").status_code == 200
    later = clock.now() + timedelta(minutes=16)
    monkeypatch.setattr(clock, "now", lambda: later)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_logout_signs_out_every_device(client: TestClient) -> None:
    first_admin(client)
    second = new_session(client)
    assert login(second, ADMIN_EMAIL) == 200
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401
    assert second.get("/api/v1/auth/me").status_code == 401
    assert second.post("/api/v1/auth/refresh").status_code == 401


def test_password_change_revokes_other_sessions_but_keeps_this_one(client: TestClient) -> None:
    first_admin(client)
    other_device = new_session(client)
    assert login(other_device, ADMIN_EMAIL) == 200
    new_password = "Another-Strong-Pass-77"
    response = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": STRONG_PASSWORD, "new_password": new_password},
    )
    assert response.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 200
    assert other_device.get("/api/v1/auth/me").status_code == 401
    assert login(new_session(client), ADMIN_EMAIL, STRONG_PASSWORD) == 401
    assert login(new_session(client), ADMIN_EMAIL, new_password) == 200


def test_password_change_needs_the_current_password(client: TestClient) -> None:
    first_admin(client)
    response = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "wrong-one-12345", "new_password": "Another-Strong-Pass-77"},
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    ("password", "code"),
    [
        ("short1", "password_too_short"),
        ("x" * 129, "password_too_long"),
        ("password1234", "password_too_common"),
        ("admin@example.com", "password_equals_email"),
    ],
)
def test_password_policy_is_enforced_on_first_run(
    client: TestClient, password: str, code: str
) -> None:
    with_csrf(client)
    response = client.post(
        "/api/v1/auth/first-admin",
        json={
            "email": ADMIN_EMAIL,
            "password": password,
            "display_name": "A",
            "workspace_name": "W",
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == code


def test_preferences_are_stored_per_user(client: TestClient) -> None:
    first_admin(client)
    response = client.patch(
        "/api/v1/auth/me", json={"language": "en", "theme": "dark", "reading_level": "expert"}
    )
    assert response.json()["user"]["theme"] == "dark"
    again = client.get("/api/v1/auth/me").json()["user"]
    assert (again["language"], again["theme"], again["reading_level"]) == ("en", "dark", "expert")
    assert client.patch("/api/v1/auth/me", json={"theme": "neon"}).status_code == 422
    assert client.patch("/api/v1/auth/me", json={"role": "admin"}).status_code == 422
