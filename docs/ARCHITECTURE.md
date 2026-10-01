# Finalyzer v3 – Architecture

## Layout

```
finalyzer/
├── core/            v2 engine (Python): ingest, semantic layer, NLU, SQL, insights. Reused by v3.
├── app/             v2 Streamlit UI (legacy; kept green until the React app replaces it)
├── backend/         v3 API (FastAPI) — package `app` (see "Two packages named app")
│   ├── app/         config, db, deps, errors, i18n, logging, security/, models/, services/, routers/
│   ├── alembic/     migrations
│   └── tests/       backend tests (run from `backend/`)
├── frontend/        v3 SPA (React + Vite + TypeScript + Tailwind)
├── shared/          fixtures used by both sides (format cases, glossary, frames)
├── tests/           v2 tests (run from the repository root)
└── docs/
```

## Tested versions (baseline, 2026-10-01)

| Part | Version |
|---|---|
| Python | 3.12.10 (two venvs: root `.venv` for v2, `backend/.venv` for v3) |
| Node / npm | 24.21.0 / 11.19.0 |
| FastAPI / Uvicorn | 0.142.2 / 0.54.0 |
| SQLAlchemy / Alembic | 2.1.1 / 1.20.0 |
| Pydantic / pydantic-settings | 2.13.5 / 2.15.0 |
| argon2-cffi / PyJWT | 25.1.0 / 2.15.1 |
| DuckDB / pandas | 1.5.6 / 2.3.3 |
| React / Vite / TypeScript | 18.3 / 8.3 / 6.0 |
| Tailwind | 4.3 |

## Baseline results

* v2 suite (root): **767 passed**, ruff clean.
* v3 backend suite (`backend/`): **183 passed**, ruff clean.

## Decisions that shape the code

* **One origin.** FastAPI serves the built SPA and the API (`/api/v1`). No CORS, cookies are
  `SameSite=Strict`, CSP is `default-src 'self'` (D2, D4).
* **One worker.** The rate limiter and the audit-chain lock live in memory (D3).
* **Auth.** argon2id (calibrated to ~250 ms at start), 15-minute HS256 access JWT in an
  HttpOnly cookie, 7-day refresh token (hashed, rotated, reuse revokes the whole family),
  signed double-submit CSRF token plus an Origin check.
* **Lockout.** Per account and per address: 5 free failures, then 1, 2, 4 … 30 minutes.
  A success clears the account counter; any counter is forgotten after 60 quiet minutes.
  Sign-in attempts are rate limited per address even when the caller holds a valid cookie.
* **Audit.** One SHA-256 hash chain per workspace and one `system` chain. Details hold ids
  and short codes only. `GET /admin/audit/verify` recomputes the chain.
* **Errors.** RFC 9457 problem documents in Uzbek (default) or English with a correlation id.
  Validation errors list fields and error types, never the submitted values.
* **Logs.** JSON lines with a request id. Exceptions are logged as type and frames only.
  HTTP-client libraries are held at WARNING because they log URLs with query strings.
* **Migrations.** Alembic with `render_as_batch` (SQLite and PostgreSQL). A test compares the
  migration result with the models and runs downgrade/upgrade.

## Two packages named `app`

The v2 Streamlit UI is `app/` at the repository root and the v3 backend package is
`backend/app/`. Python cannot import both in one process, so the suites run separately:

```
.venv\Scripts\python.exe -m pytest                       # v2, from the repository root
cd backend; .venv\Scripts\python.exe -m pytest           # v3, from backend/
```

The backend adds the repository root to `sys.path` (second place) to import `core/`.
The Streamlit `app/` is removed in the last phase; the package clash then disappears.

## Legacy plan

`core/` stays and is imported by the backend. `app/` (Streamlit) stays green until phase U13
and is then deleted together with its tests. No v2 file is changed except for bug fixes.
