"""S16 logs, S19 error responses, spec 5.1 catalogs, Accept-Language."""

from __future__ import annotations

import io
import json
import logging
import re

import pytest
from app.errors import ERROR_STATUS
from app.logging_setup import JsonFormatter
from fastapi.testclient import TestClient

from app.i18n import LOCALES_DIR, catalog, parse_accept_language, translate
from tests.conftest import ADMIN_EMAIL, STRONG_PASSWORD, first_admin, new_session, with_csrf

PLACEHOLDER = re.compile(r"\{(\w+)\}")
CANARY_PASSWORD = "CANARY-Password-7731-zz"
CANARY_VALUE = "CANARY-secret-value-9912"


@pytest.fixture
def log_stream():
    """Capture what the JSON log handler would print."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    previous = root.level
    root.setLevel(logging.DEBUG)
    yield stream
    root.removeHandler(handler)
    root.setLevel(previous)


# ---------------------------------------------------------------- catalogs


def test_catalogs_have_the_same_keys_and_placeholders() -> None:
    uz, en = catalog("uz"), catalog("en")
    assert set(uz) == set(en)
    for key in uz:
        assert set(PLACEHOLDER.findall(uz[key])) == set(PLACEHOLDER.findall(en[key])), key


def test_every_error_code_has_a_title_and_detail_in_both_languages() -> None:
    for locale in ("uz", "en"):
        for code in ERROR_STATUS:
            for part in ("title", "detail"):
                assert f"errors.{code}.{part}" in catalog(locale), (locale, code, part)


def test_uzbek_text_uses_the_right_apostrophes() -> None:
    """oʻ and gʻ use U+02BB; a plain or typographic apostrophe after o or g is wrong."""
    wrong = re.compile(r"[oOgG][\u0027\u2018\u2019\u0060\u00b4]")
    for key, value in catalog("uz").items():
        assert not wrong.search(value), (key, value)


def test_uzbek_text_is_actually_uzbek() -> None:
    assert translate("uz", "errors.forbidden.title") != translate("en", "errors.forbidden.title")
    assert "ʻ" in json.loads((LOCALES_DIR / "uz.json").read_text(encoding="utf-8")).get(
        "errors.rate_limited.title", ""
    )


def test_missing_keys_fall_back_without_crashing() -> None:
    assert translate("en", "no.such.key") == "no.such.key"
    assert translate("de", "errors.forbidden.title") == translate("uz", "errors.forbidden.title")
    # A placeholder without a value stays as written instead of raising.
    assert "{min}" in translate("uz", "errors.password_too_short.detail")


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, "uz"),
        ("", "uz"),
        ("en", "en"),
        ("en-US,en;q=0.9", "en"),
        ("uz-UZ", "uz"),
        ("ru-RU,ru;q=0.9,en;q=0.8", "en"),
        ("fr", "uz"),
        ("en;q=0.5,uz;q=0.9", "uz"),
        ("en;q=abc", "uz"),
        ("*", "uz"),
        ("en;q=0", "uz"),
    ],
)
def test_accept_language(header: str | None, expected: str) -> None:
    assert parse_accept_language(header) == expected


# ---------------------------------------------------------------- problem details


def test_errors_are_problem_details_in_uzbek_by_default(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")
    body = response.json()
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert body["type"] == "urn:finalyzer:problem:not_authenticated"
    assert body["title"] == translate("uz", "errors.not_authenticated.title")
    assert body["correlation_id"] == response.headers["x-request-id"]
    assert body["instance"] == "/api/v1/auth/me"


def test_errors_follow_accept_language(client: TestClient) -> None:
    body = client.get("/api/v1/auth/me", headers={"Accept-Language": "en"}).json()
    assert body["title"] == translate("en", "errors.not_authenticated.title")


def test_unknown_api_paths_and_methods_are_problem_documents(client: TestClient) -> None:
    missing = client.get("/api/v1/nothing-here")
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"
    wrong_method = client.put("/api/v1/healthz")
    assert wrong_method.status_code in {403, 405}  # CSRF is checked first for unsafe methods
    assert wrong_method.json()["type"].startswith("urn:finalyzer:problem:")


def test_validation_errors_list_fields_but_not_the_submitted_values(client: TestClient) -> None:
    with_csrf(client)
    response = client.post(
        "/api/v1/auth/login", json={"email": CANARY_VALUE, "password": CANARY_PASSWORD, "x": 1}
    )
    assert response.status_code == 422
    assert CANARY_VALUE not in response.text
    assert CANARY_PASSWORD not in response.text
    assert response.json()["errors"]


def test_a_request_id_from_the_client_is_kept_only_when_it_looks_safe(client: TestClient) -> None:
    good = client.get("/api/v1/healthz", headers={"X-Request-ID": "abc-12345678"})
    assert good.headers["x-request-id"] == "abc-12345678"
    bad = client.get("/api/v1/healthz", headers={"X-Request-ID": "x y\t<script>"})
    assert re.fullmatch(r"[0-9a-f]{16}", bad.headers["x-request-id"])


def add_crashing_route(client: TestClient) -> None:
    def crash() -> None:
        raise RuntimeError(f"database said: {CANARY_VALUE}")

    client.app.add_api_route("/api/v1/_crash", crash, methods=["GET"])
    routes = client.app.router.routes
    routes.insert(0, routes.pop())  # the SPA catch-all would otherwise answer first


def test_an_unexpected_error_gives_a_clean_500_without_details(
    client: TestClient, log_stream
) -> None:
    add_crashing_route(client)
    response = client.get("/api/v1/_crash")
    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "server_error"
    assert body["correlation_id"] == response.headers["x-request-id"]
    for leaked in (CANARY_VALUE, "Traceback", "RuntimeError", "def crash", ".py", "File "):
        assert leaked not in response.text, leaked
    # The log has the type and frames, but not the message.
    logs = log_stream.getvalue()
    assert "RuntimeError" in logs
    assert CANARY_VALUE not in logs
    assert body["correlation_id"] in logs


def test_the_500_also_carries_the_security_headers(client: TestClient) -> None:
    add_crashing_route(client)
    response = client.get("/api/v1/_crash")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" in response.headers


# ---------------------------------------------------------------- log canary


def test_logs_never_contain_passwords_tokens_cookies_or_bodies(
    client: TestClient, log_stream
) -> None:
    first_admin(client)
    browser = with_csrf(new_session(client))
    browser.post(
        "/api/v1/auth/login",
        json={"email": ADMIN_EMAIL, "password": CANARY_PASSWORD},
        headers={"X-Canary-Header": CANARY_VALUE},
    )
    browser.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": STRONG_PASSWORD})
    browser.get(f"/api/v1/auth/me?token={CANARY_VALUE}")
    logs = log_stream.getvalue()
    assert logs, "the access log must not be empty"
    cookies = " ".join(f"{c.value}" for c in browser.cookies.jar if len(c.value) > 20)
    for forbidden in (CANARY_PASSWORD, CANARY_VALUE, STRONG_PASSWORD, ADMIN_EMAIL, cookies):
        assert forbidden not in logs, forbidden


def test_every_log_line_is_json_with_a_request_id(client: TestClient, log_stream) -> None:
    client.get("/api/v1/healthz")
    lines = [line for line in log_stream.getvalue().splitlines() if line.strip()]
    assert lines
    for line in lines:
        entry = json.loads(line)
        assert {"ts", "level", "logger", "request_id", "msg"} <= set(entry)
    assert any("path=/api/v1/healthz" in json.loads(line)["msg"] for line in lines)
