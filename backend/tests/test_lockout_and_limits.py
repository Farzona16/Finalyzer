"""S2 lockout and S7 rate limits."""

from __future__ import annotations

from datetime import timedelta

import pytest
from app.security.ratelimit import Policy, RateLimiter
from app.security.throttle import lock_minutes
from fastapi.testclient import TestClient

from app import clock
from tests.conftest import ADMIN_EMAIL, STRONG_PASSWORD, first_admin, login, new_session, with_csrf


def failed_login(client: TestClient, email: str = ADMIN_EMAIL):
    return client.post("/api/v1/auth/login", json={"email": email, "password": "wrong-password-1"})


def test_lock_minutes_grow_exponentially_up_to_thirty() -> None:
    assert [lock_minutes(n) for n in range(1, 12)] == [0, 0, 0, 0, 1, 2, 4, 8, 16, 30, 30]


def test_five_failures_lock_the_account_even_for_the_right_password(client: TestClient) -> None:
    first_admin(client)
    attacker = with_csrf(new_session(client))
    for _ in range(5):
        assert failed_login(attacker).json()["code"] == "invalid_credentials"
    locked = failed_login(attacker)
    assert locked.status_code == 429
    assert locked.json()["code"] == "account_locked"
    assert int(locked.headers["retry-after"]) > 0
    # The legitimate user is locked as well, until the lock ends.
    assert login(new_session(client), ADMIN_EMAIL) == 429


def test_the_lock_ends_and_the_next_lock_is_longer(client: TestClient, monkeypatch) -> None:
    first_admin(client)
    attacker = with_csrf(new_session(client))
    for _ in range(5):
        failed_login(attacker)
    start = clock.now()
    monkeypatch.setattr(clock, "now", lambda: start + timedelta(minutes=1, seconds=1))
    assert failed_login(attacker).json()["code"] == "invalid_credentials"  # 6th failure
    again = failed_login(attacker)
    assert again.json()["code"] == "account_locked"
    assert 60 < int(again.headers["retry-after"]) <= 120  # now two minutes


def test_a_successful_login_resets_the_account_counter(make_client) -> None:
    proxied = make_client(trust_proxy_headers=True)
    first_admin(proxied)
    browser = with_csrf(new_session(proxied))
    browser.headers["X-Forwarded-For"] = "10.0.0.1"
    for _ in range(4):
        failed_login(browser)
    assert login(browser, ADMIN_EMAIL) == 200
    other_address = with_csrf(new_session(proxied))
    other_address.headers["X-Forwarded-For"] = "10.0.0.2"  # keeps the address counter apart
    for _ in range(4):
        failed_login(other_address)
    assert login(other_address, ADMIN_EMAIL) == 200  # 4 + 4 failures, but reset in between


def test_old_failures_are_forgotten_after_an_hour(client: TestClient, monkeypatch) -> None:
    first_admin(client)
    attacker = with_csrf(new_session(client))
    for _ in range(4):
        failed_login(attacker)
    start = clock.now()
    monkeypatch.setattr(clock, "now", lambda: start + timedelta(minutes=61))
    for _ in range(4):
        assert failed_login(attacker).json()["code"] == "invalid_credentials"
    assert login(new_session(client), ADMIN_EMAIL) == 200


def test_unknown_accounts_are_locked_the_same_way(client: TestClient) -> None:
    attacker = with_csrf(new_session(client))
    for _ in range(5):
        failed_login(attacker, "ghost@example.com")
    assert failed_login(attacker, "ghost@example.com").json()["code"] == "account_locked"


def test_the_address_is_locked_across_accounts(client: TestClient) -> None:
    first_admin(client)
    attacker = with_csrf(new_session(client))
    for index in range(5):
        failed_login(attacker, f"user{index}@example.com")
    assert failed_login(attacker, "another@example.com").json()["code"] == "account_locked"


def test_login_is_limited_to_five_requests_per_minute(make_client) -> None:
    limited = make_client(rate_limit_login_per_min=5)
    first_admin(limited)  # first-admin shares the login policy: 1 of 5 used, signed in now
    # A valid cookie of one's own account gives no extra sign-in attempts (always per address).
    codes = [
        limited.post(
            "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": STRONG_PASSWORD}
        ).status_code
        for _ in range(6)
    ]
    assert codes == [200, 200, 200, 200, 429, 429]
    response = limited.post(
        "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": STRONG_PASSWORD}
    )
    assert response.json()["code"] == "rate_limited"
    assert int(response.headers["retry-after"]) >= 1


def test_the_api_limit_counts_per_user(make_client) -> None:
    limited = make_client(rate_limit_api_per_min=10)
    first_admin(limited)  # the cookie is set now: the user counter starts empty
    codes = [limited.get("/api/v1/auth/me").status_code for _ in range(12)]
    assert codes[:10] == [200] * 10
    assert codes[10:] == [429, 429]


def test_limiter_window_slides_and_reports_retry_after() -> None:
    now = [0.0]
    limiter = RateLimiter({"x": Policy("x", 2, 60)}, timer=lambda: now[0])
    limiter.check("x", "k")
    now[0] = 10
    limiter.check("x", "k")
    with pytest.raises(Exception) as caught:  # noqa: PT011 - AppError carries the headers
        limiter.check("x", "k")
    assert caught.value.headers["Retry-After"] == "50"
    limiter.check("x", "other-key")  # keys are independent
    now[0] = 61
    limiter.check("x", "k")  # the first hit has left the window


def test_limiter_forgets_idle_keys() -> None:
    now = [0.0]
    limiter = RateLimiter({"x": Policy("x", 1, 10)}, timer=lambda: now[0])
    for index in range(10_005):
        limiter.check("x", f"key-{index}")
    now[0] = 100
    limiter.check("x", "fresh")
    assert len(limiter._hits) < 100
