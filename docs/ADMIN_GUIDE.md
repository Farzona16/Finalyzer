# Admin guide

## First administrator

If the database is empty, `POST /api/v1/auth/first-admin` (the `/setup` page) creates the
workspace and the admin. CLI from `backend/`:

```bash
python -m app.cli generate-keys
python -m app.cli create-admin --email admin@example.com --name 'Admin' --workspace 'Acme'
```

The command asks for the password twice (it is never passed on the command line).

## Roles

| Role | Can |
| --- | --- |
| viewer | dashboards, insights, Ask, privacy page |
| analyst | + upload, build, KPI edits, see SQL |
| admin | + users, policy, audit chain |

Do not deactivate the last admin.

## Privacy policy

`GET/PUT /api/v1/admin/policy` — workspace privacy mode must be in `ALLOWED_PRIVACY_MODES`.
Offline / demo should keep `LLM_MODE=offline` or an empty key for public demos.

## Audit

`GET /api/v1/admin/audit` and `GET /api/v1/admin/audit/verify`. A broken chain means the
log was tampered with; treat it as an incident.

## Storage

- `STORAGE_MODE=ephemeral` (default): datasets live in a temp dir and are deleted on idle
  (`SESSION_IDLE_MINUTES`, default 30) and on process restart.
- `STORAGE_MODE=persistent`: encrypted Parquet under `DATA_DIR`. Requires `APP_MASTER_KEY`
  (32 bytes, base64). Redeploys without a disk lose the files.

## Retention and backups

Retention is a workspace policy setting. Backups are an operations procedure: snapshot the
Postgres volume (accounts) and, in persistent mode, the `DATA_DIR` volume. This document
is not a backup SLA.

## Key rotation (persistent mode)

1. Generate a new `APP_MASTER_KEY`.
2. Re-encrypt stored datasets with a planned maintenance window (export, then re-import),
   or keep the old key until every file is rewritten. There is no in-place rewrap job yet
   (`KNOWN_LIMITATIONS.md`).

## Legal drafts

Terms and Privacy pages, if present, are drafts for a lawyer. They are not a claim of
compliance.
