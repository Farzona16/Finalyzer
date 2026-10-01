# Security

This is a description of controls, not a certification or a claim of compliance.

## S-row mapping (automated tests unless noted)

| ID | Control | Test or document |
| --- | --- | --- |
| S1 | argon2id passwords, min 12, common-password list | `backend/tests/test_tokens_and_passwords.py` |
| S2 | lockout after 5 failures | `backend/tests/test_lockout_and_limits.py` |
| S3 | JWT + hashed rotating refresh, reuse detection | `backend/tests/test_auth_flow.py` |
| S4 | httpOnly, Secure (production), SameSite | `backend/tests/test_auth_flow.py` |
| S5 | double-submit CSRF + Origin | `backend/tests/test_csrf_and_routes.py` |
| S6 | role matrix, workspace scope, IDOR from OpenAPI | `backend/tests/test_roles_and_admin.py`, `test_csrf_and_routes.py` |
| S7 | rate limits (login, API, upload, Ask) | `backend/tests/test_lockout_and_limits.py` |
| S8 | CSP and security headers | `backend/tests/test_security_headers.py` |
| S9 | upload magic bytes, zip-bomb, size | `backend/tests/test_datasets_upload.py` |
| S10 | AES-256-GCM at rest | `backend/tests/test_storage_lifecycle.py` |
| S11 | audit hash chain | `backend/tests/test_audit_chain.py` |
| S12 | egress gate + privacy counters | `backend/tests/test_insights_privacy.py`, `tests/test_egress.py` |
| S13 | parameterized ORM; no raw HTML in React | `frontend/eslint.config.js`, v2 filter tests |
| S14 | prompts do not execute model output except validated SQL | `tests/test_sql_validator.py`, Ask compiler |
| S15 | secrets from env; fail closed | `backend/tests/test_config.py` |
| S16 | structured logs, canary secret | `backend/tests/test_errors_logs_i18n.py` |
| S17 | hard delete, idle purge | `backend/tests/test_storage_lifecycle.py` |
| S18 | `scripts/security_check.sh` | pip-audit, npm audit, bandit, eslint-security |
| S19 | problem-details errors, no stack traces | `backend/tests/test_errors_logs_i18n.py` |
| S20 | TLS at the proxy; production refuses insecure cookies | `deploy/Caddyfile`, `test_config.py` |
| S21 | legal drafts (doc) | `docs/ADMIN_GUIDE.md` |
| S22 | backups (doc) | `docs/ADMIN_GUIDE.md` |

The mapping is asserted by `backend/tests/test_security_map.py`.

## Transport

Render terminates TLS. On-prem: Caddy (`deploy/Caddyfile`) in front of Uvicorn.
The app sets `Secure` cookies when `APP_ENV=production`.

## Dependency audits

Run `scripts/security_check.sh` and keep `reports/security_check.txt`.
`scripts/secret_scan.py` writes `reports/secret_scan.txt`.
