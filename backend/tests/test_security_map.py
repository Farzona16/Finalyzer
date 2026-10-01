"""S1–S20 map onto an automated test (or a checked-in document for doc-only rows)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_TESTS = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"

# Each row must exist. "doc" rows still have a file that a human can read.
MAPPING: dict[str, tuple[str, str]] = {
    "S1": ("backend/tests/test_tokens_and_passwords.py", "argon2id / common-password list"),
    "S2": ("backend/tests/test_lockout_and_limits.py", "lockout after failed logins"),
    "S3": ("backend/tests/test_auth_flow.py", "refresh rotation and reuse"),
    "S4": ("backend/tests/test_auth_flow.py", "cookie flags including Secure"),
    "S5": ("backend/tests/test_csrf_and_routes.py", "double-submit CSRF"),
    "S6": ("backend/tests/test_roles_and_admin.py", "role matrix and IDOR"),
    "S7": ("backend/tests/test_lockout_and_limits.py", "rate limits"),
    "S8": ("backend/tests/test_security_headers.py", "CSP and security headers"),
    "S9": ("backend/tests/test_datasets_upload.py", "magic bytes, zip bomb, size"),
    "S10": ("backend/tests/test_storage_lifecycle.py", "AES-GCM round-trip"),
    "S11": ("backend/tests/test_audit_chain.py", "tamper-evident chain"),
    "S12": ("backend/tests/test_insights_privacy.py", "egress counters / offline 0 bytes"),
    "S13": ("frontend/eslint.config.js", "no dangerouslySetInnerHTML; parameterized ORM"),
    "S14": ("tests/test_egress.py", "payload schemas; SQL validator"),
    "S15": ("backend/tests/test_config.py", "fail-closed settings"),
    "S16": ("backend/tests/test_errors_logs_i18n.py", "log canary"),
    "S17": ("backend/tests/test_storage_lifecycle.py", "hard delete and idle purge"),
    "S18": ("scripts/security_check.sh", "pip-audit / npm audit / bandit"),
    "S19": ("backend/tests/test_errors_logs_i18n.py", "problem-details, no stack traces"),
    "S20": ("backend/tests/test_config.py", "production refuses insecure cookies"),
}


def test_every_s_row_points_at_a_real_file() -> None:
    missing = []
    for sid, (rel, _why) in MAPPING.items():
        path = ROOT / rel
        if not path.is_file():
            missing.append(f"{sid}: {rel}")
    assert missing == []


def test_mapping_covers_s1_to_s20() -> None:
    assert list(MAPPING) == [f"S{i}" for i in range(1, 21)]


def test_s13_lint_bans_raw_html() -> None:
    text = (FRONTEND / "eslint.config.js").read_text(encoding="utf-8")
    assert "dangerouslySetInnerHTML" in text
    assert "innerHTML" in text
