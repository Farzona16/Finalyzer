"""S8: security headers on every kind of response."""

from __future__ import annotations

import pytest
from app.security.middleware import CONTENT_SECURITY_POLICY
from fastapi.testclient import TestClient

EXPECTED = {
    "content-security-policy": CONTENT_SECURITY_POLICY,
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "cross-origin-opener-policy": "same-origin",
}


def assert_headers(response) -> None:  # noqa: ANN001 - httpx response
    for name, value in EXPECTED.items():
        assert response.headers[name] == value, name
    assert "camera=()" in response.headers["permissions-policy"]


@pytest.mark.parametrize("path", ["/", "/some/client/route", "/api/v1/healthz", "/api/v1/nope"])
def test_every_response_has_the_headers(client: TestClient, path: str) -> None:
    assert_headers(client.get(path))


def test_csp_follows_the_spec_exactly(client: TestClient) -> None:
    csp = client.get("/").headers["content-security-policy"]
    assert csp == (
        "default-src 'self'; img-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    )
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp


def test_api_responses_are_not_cached(client: TestClient) -> None:
    assert client.get("/api/v1/healthz").headers["cache-control"] == "no-store"


def test_hsts_only_over_https(client: TestClient, make_client) -> None:
    assert "strict-transport-security" not in client.get("/").headers
    secure = TestClient(client.app, base_url="https://testserver")
    assert "max-age=31536000" in secure.get("/").headers["strict-transport-security"]


def test_hsts_from_a_trusted_proxy_header(make_client) -> None:
    behind_proxy = make_client(trust_proxy_headers=True)
    response = behind_proxy.get("/", headers={"X-Forwarded-Proto": "https"})
    assert "strict-transport-security" in response.headers
    plain = make_client(trust_proxy_headers=False)
    assert (
        "strict-transport-security"
        not in plain.get("/", headers={"X-Forwarded-Proto": "https"}).headers
    )


def test_error_responses_also_carry_headers_and_request_id(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert_headers(response)
    assert response.headers["x-request-id"] == response.json()["correlation_id"]


def test_request_id_from_the_client_is_kept_only_when_well_formed(client: TestClient) -> None:
    good = client.get("/api/v1/healthz", headers={"X-Request-ID": "trace-12345678"})
    assert good.headers["x-request-id"] == "trace-12345678"
    bad = client.get("/api/v1/healthz", headers={"X-Request-ID": 'x"><script>'})
    assert bad.headers["x-request-id"] != 'x"><script>'
