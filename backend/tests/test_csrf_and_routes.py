"""S5 CSRF, S6 route protection (generated from the real route table), IDOR."""

from __future__ import annotations

import re

import pytest
from app.deps import PUBLIC_ROUTES
from app.security.csrf import issue_token
from fastapi.testclient import TestClient

from tests.conftest import (
    ADMIN_EMAIL,
    SECRET,
    STRONG_PASSWORD,
    first_admin,
    insert_workspace_with_admin,
    new_session,
    with_csrf,
)

LOGIN = {"email": ADMIN_EMAIL, "password": STRONG_PASSWORD}


def api_routes(client: TestClient) -> list[tuple[str, str]]:
    """(METHOD, path) of every operation under /api/v1, read from the OpenAPI document."""
    paths = client.app.openapi()["paths"]
    return [
        (method.upper(), path)
        for path, operations in paths.items()
        if path.startswith("/api/v1")
        for method in operations
        if method in {"get", "post", "put", "patch", "delete"}
    ]


def fill(path: str) -> str:
    """Replace path parameters with a harmless value."""
    return re.sub(r"\{[^}]+\}", "x" * 32, path)


# ---------------------------------------------------------------- CSRF


def test_a_post_without_a_token_is_refused(client: TestClient) -> None:
    first_admin(client)
    plain = new_session(client)
    response = plain.post("/api/v1/auth/login", json=LOGIN)
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"


def test_header_and_cookie_must_match(client: TestClient) -> None:
    first_admin(client)
    browser = new_session(client)
    browser.get("/api/v1/auth/csrf")
    other = issue_token(SECRET)  # validly signed, but not the one in the cookie
    response = browser.post("/api/v1/auth/login", json=LOGIN, headers={"X-CSRF-Token": other})
    assert response.json()["code"] == "csrf_failed"


def test_an_unsigned_token_is_refused_even_when_cookie_and_header_match(
    client: TestClient,
) -> None:
    first_admin(client)
    browser = new_session(client)
    forged = "attacker-chosen.value"
    browser.cookies.set("fz_csrf", forged)
    response = browser.post("/api/v1/auth/login", json=LOGIN, headers={"X-CSRF-Token": forged})
    assert response.json()["code"] == "csrf_failed"


def test_a_token_signed_with_another_secret_is_refused(client: TestClient) -> None:
    first_admin(client)
    browser = new_session(client)
    foreign = issue_token("some-other-secret-0123456789-abcdefghijklmnopqrstuvwxyz")
    browser.cookies.set("fz_csrf", foreign)
    response = browser.post("/api/v1/auth/login", json=LOGIN, headers={"X-CSRF-Token": foreign})
    assert response.json()["code"] == "csrf_failed"


@pytest.mark.parametrize(
    "origin", ["https://evil.example", "null", "http://testserver.evil.example"]
)
def test_a_foreign_origin_is_refused(client: TestClient, origin: str) -> None:
    first_admin(client)
    browser = with_csrf(new_session(client))
    response = browser.post("/api/v1/auth/login", json=LOGIN, headers={"Origin": origin})
    assert response.status_code == 403
    assert response.json()["code"] == "origin_not_allowed"


def test_the_same_origin_is_accepted(client: TestClient) -> None:
    first_admin(client)
    browser = with_csrf(new_session(client))
    response = browser.post(
        "/api/v1/auth/login", json=LOGIN, headers={"Origin": "http://testserver"}
    )
    assert response.status_code == 200


def test_every_unsafe_route_demands_a_token(client: TestClient) -> None:
    first_admin(client)
    bare = new_session(client)
    bare.cookies.update(client.cookies)  # signed in, but no CSRF token
    for method, path in api_routes(client):
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            response = bare.request(method, fill(path), json={})
            assert response.status_code == 403, (method, path)
            assert response.json()["code"] == "csrf_failed", (method, path)


def test_safe_methods_need_no_token(client: TestClient) -> None:
    assert client.get("/api/v1/healthz").status_code == 200


# ---------------------------------------------------------------- route protection


def test_the_public_list_matches_the_real_routes(client: TestClient) -> None:
    """A route added without thought must not be public by accident."""
    assert PUBLIC_ROUTES <= set(api_routes(client)), "PUBLIC_ROUTES lists a route that is gone"


def test_every_route_outside_the_public_list_refuses_anonymous_calls(
    client: TestClient,
) -> None:
    first_admin(client)
    anonymous = with_csrf(new_session(client))
    checked = 0
    for method, path in api_routes(client):
        if (method, path) in PUBLIC_ROUTES:
            continue
        response = anonymous.request(method, fill(path), json={})
        assert response.status_code == 401, (method, path, response.status_code)
        assert response.json()["code"] == "not_authenticated", (method, path)
        checked += 1
    assert checked >= 10


def test_a_garbage_or_foreign_token_is_not_authentication(client: TestClient) -> None:
    first_admin(client)
    browser = new_session(client)
    browser.cookies.set("fz_access", "not.a.jwt", path="/api/v1")
    assert browser.get("/api/v1/auth/me").status_code == 401


# ---------------------------------------------------------------- IDOR


def test_object_ids_of_another_workspace_are_never_reachable(client: TestClient) -> None:
    """Every route with an id in its path is called with an id of another workspace."""
    first_admin(client)
    foreign_id = insert_workspace_with_admin(client, "boss@other.example")
    checked = 0
    for method, path in api_routes(client):
        if "{" not in path or (method, path) in PUBLIC_ROUTES:
            continue
        target = re.sub(r"\{[^}]+\}", foreign_id, path)
        response = client.request(method, target, json={})
        assert response.status_code == 404, (method, path, response.status_code)
        checked += 1
    assert checked >= 2
    # The foreign user is untouched.
    from app.db import session_scope
    from app.models import User
    from sqlalchemy import select

    with session_scope(client.app.state.session_factory) as db:
        foreign = db.scalar(select(User).where(User.id == foreign_id))
        assert foreign is not None
        assert foreign.is_active and foreign.role == "admin"
