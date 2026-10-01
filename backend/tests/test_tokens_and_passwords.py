"""S1 passwords and S3 tokens at unit level."""

from __future__ import annotations

import time
from datetime import timedelta
from types import SimpleNamespace

import jwt
import pytest
from app.errors import AppError
from app.security.passwords import (
    MAX_LENGTH,
    PasswordService,
    calibrated_hasher,
    check_password_policy,
    common_passwords,
    fast_hasher,
    is_common,
)
from app.security.tokens import (
    ALGORITHM,
    AUDIENCE,
    ISSUER,
    create_access_token,
    decode_access_token,
    hash_refresh_token,
    new_refresh_token,
)

from app import clock
from tests.conftest import SECRET, STRONG_PASSWORD


@pytest.fixture
def settings(make_settings):
    return make_settings()


@pytest.fixture
def user():
    return SimpleNamespace(id="u" * 32, workspace_id="w" * 32, role="analyst", token_version=3)


def forged(settings, **changes):
    now = int(clock.now().timestamp())
    payload = {
        "sub": "u" * 32,
        "wid": "w" * 32,
        "role": "admin",
        "tv": 0,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 600,
    }
    payload.update(changes)
    return payload


# ---------------------------------------------------------------- passwords


def test_hashes_are_argon2id_and_salted() -> None:
    service = PasswordService(fast_hasher())
    first, second = service.hash(STRONG_PASSWORD), service.hash(STRONG_PASSWORD)
    assert first.startswith("$argon2id$")
    assert first != second
    assert service.verify(first, STRONG_PASSWORD)
    assert not service.verify(first, STRONG_PASSWORD + "x")


def test_verifying_against_nothing_does_not_crash_and_is_false() -> None:
    service = PasswordService(fast_hasher())
    assert service.verify(None, "anything-at-all-1") is False
    assert service.verify("not-a-hash", "anything-at-all-1") is False


def test_the_production_hasher_is_calibrated_and_within_bounds() -> None:
    started = time.perf_counter()
    hasher = calibrated_hasher(target_ms=40)
    assert time.perf_counter() - started < 20
    assert hasher.time_cost >= 2
    assert hasher.memory_cost >= 19456  # never weaker than the OWASP minimum


@pytest.mark.parametrize(
    ("password", "code"),
    [
        ("short", "password_too_short"),
        ("a" * 11, "password_too_short"),
        ("x" * (MAX_LENGTH + 1), "password_too_long"),
        ("password1234", "password_too_common"),
        ("qwertyuiop1", "password_too_short"),
    ],
)
def test_password_policy_rules(password: str, code: str) -> None:
    with pytest.raises(AppError) as caught:
        check_password_policy(password)
    assert caught.value.code == code


def test_a_password_equal_to_the_email_is_refused() -> None:
    with pytest.raises(AppError) as caught:
        check_password_policy("Admin@Example.com", "admin@example.com")
    assert caught.value.code == "password_equals_email"


def test_a_long_passphrase_and_unicode_are_accepted() -> None:
    check_password_policy("correct horse battery staple")
    check_password_policy("Oʻzbekiston-gʻalaba-2026")
    check_password_policy(STRONG_PASSWORD, "someone@example.com")
    check_password_policy("x" * MAX_LENGTH)


def test_the_embedded_list_is_the_top_10000() -> None:
    assert len(common_passwords()) >= 9_000
    assert is_common("123456") and is_common("PASSWORD")
    assert not is_common(STRONG_PASSWORD)


# ---------------------------------------------------------------- access tokens


def test_a_fresh_token_decodes(settings, user) -> None:
    token, expires = create_access_token(settings, user)
    claims = decode_access_token(settings, token)
    assert claims is not None
    assert (claims.user_id, claims.workspace_id, claims.role, claims.token_version) == (
        user.id,
        user.workspace_id,
        "analyst",
        3,
    )
    assert expires - clock.now() <= timedelta(minutes=15)


def test_an_expired_token_is_refused(settings, user, monkeypatch) -> None:
    token, _ = create_access_token(settings, user)
    start = clock.now()
    monkeypatch.setattr(clock, "now", lambda: start + timedelta(minutes=16))
    assert decode_access_token(settings, token) is None


def test_the_none_algorithm_is_refused(settings) -> None:
    token = jwt.encode(forged(settings), key=None, algorithm="none")
    assert decode_access_token(settings, token) is None


def test_a_token_signed_with_another_key_is_refused(settings) -> None:
    token = jwt.encode(
        forged(settings), "another-key-0123456789-abcdefghijklmnopqrstuvwxyz", ALGORITHM
    )
    assert decode_access_token(settings, token) is None


def test_a_different_algorithm_is_refused(settings) -> None:
    token = jwt.encode(forged(settings), SECRET, "HS512")
    assert decode_access_token(settings, token) is None


def test_a_changed_payload_is_refused(settings, user) -> None:
    token, _ = create_access_token(settings, user)
    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload[:-2]}AA.{signature}"
    assert decode_access_token(settings, tampered) is None


@pytest.mark.parametrize("change", [{"iss": "other"}, {"aud": "other"}])
def test_wrong_issuer_or_audience_is_refused(settings, change) -> None:
    token = jwt.encode(forged(settings, **change), SECRET, ALGORITHM)
    assert decode_access_token(settings, token) is None


@pytest.mark.parametrize("missing", ["sub", "wid", "role", "tv", "exp", "iat"])
def test_required_claims_must_be_present(settings, missing) -> None:
    payload = forged(settings)
    del payload[missing]
    assert decode_access_token(settings, jwt.encode(payload, SECRET, ALGORITHM)) is None


@pytest.mark.parametrize("garbage", ["", "abc", "a.b.c", "....", "x" * 5000])
def test_garbage_never_raises(settings, garbage) -> None:
    assert decode_access_token(settings, garbage) is None


# ---------------------------------------------------------------- refresh tokens


def test_refresh_tokens_are_random_and_only_the_hash_is_derivable() -> None:
    first, first_hash = new_refresh_token()
    second, _ = new_refresh_token()
    assert first != second
    assert len(first) >= 43  # 256 bits
    assert first_hash == hash_refresh_token(first)
    assert first not in first_hash and len(first_hash) == 64
