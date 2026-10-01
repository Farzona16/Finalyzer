# FINALYZER v3 — Product-Grade Upgrade Specification (for an AI Coding Agent)

**Version:** 3.0 · **Owner:** Farzonaxon · **Author role:** Full-stack Product Engineer / Solution Architect
**Baseline:** the working v2 repository (Streamlit app + pure-Python `core/` + tests) built from `Finalyzer_Agent_Spec_EN.md` v2.0.
**Goal:** turn it into a sellable web product: modern interactive UI, dark/light themes, full Uzbek (plus English) interface, automatic KPIs, more domain templates, plain-language answers, and a hardened security posture, **without losing any v2 functionality**.

---

## 0. Agent Operating Protocol (read first, follow literally)

**R1. Do not ask questions.** All decisions are in Section 2. If something is truly unspecified, choose the simplest option that respects Section 1, add one line to `ASSUMPTIONS.md`, and continue. Stop only for a missing secret, and then continue in offline mode.
**R2. Phases U0 → U13, in order.** A phase is complete only when its acceptance tests pass. Fix code, not tests (unless a test is provably wrong; then record it).
**R3. Complete files only.** No placeholders, no `TODO`, no `...`.
**R4. Regression shield.** The entire v2 test suite (`tests/`) must stay green after every phase. `core/` keeps working without FastAPI or React imports. If you must change `core/`, add tests first.
**R5. Two modes.** *Filesystem mode:* create files, run commands and tests, proceed through all phases. *Chat mode:* one phase per reply with complete files and exact commands, ending with `PHASE n COMPLETE. Reply "next".`
**R6. Status report after every phase** (3–8 lines): files, tests run/results, deviations.
**R7. Never invent library APIs.** Check installed versions, read docs/source, write a small test. Pin versions in lock files (`requirements.lock.txt`, `package-lock.json`).
**R8. Code style.** Simple, readable code with types and short docstrings; common functions; no clever tricks; functions under ~40 lines; comments explain *why*. TypeScript `strict: true`, no `any` without a comment.
**R9. Truthfulness.** Never claim a feature works without a passing test. `KNOWN_LIMITATIONS.md` must be honest. Never claim legal or standards compliance anywhere in code, UI or docs.
**R10. Uzbek text is a product asset.** Never machine-translate UI strings ad hoc. Follow Section 5 exactly, and list every Uzbek string family in `docs/uz_review.md` for the owner's native review.

---

## 1. Product Vision

**One sentence:** Finalyzer is a private, Uzbek-first analytics workspace: upload business data, and in minutes get a beautiful interactive dashboard, automatically chosen KPIs, plain-language insights with concrete advice, and a question box that answers in simple Uzbek, while the data stays protected.

**Users:** (1) bank and finance staff; (2) shop, sales and service business owners with no BI skills; (3) analysts and students who need quick statistics.

**What "sellable" means (success criteria):**
1. A first-time user reaches a meaningful dashboard in ≤ 5 minutes with zero configuration (no KPI rules typed).
2. Everything the user reads (UI, charts, insights, answers, errors) is in clear Uzbek or English, switchable at any time.
3. Looks and feels like a modern BI product (reference: the supplied dark "Project Management Dashboard" screenshot: dark cards, vivid KPI tiles, donut, line, gauge, bullet and stacked bars). Use it as a **style reference only**; do not copy its logos, text or watermarks.
4. Real accounts, roles, audit and encrypted storage.
5. Every number is computed by code and verifiable; the AI never invents figures.

**Non-goals (v3):** billing/payments, live DB connectors, forecasting models, mobile native apps, SSO, white-label, Cyrillic Uzbek and Russian UI (architecture must allow them later without code changes), formal certifications.

---

## 2. Decision Log (binding)

| # | Decision |
|---|---|
| D1 | Keep `core/` as the engine. Add `backend/` (FastAPI) and `frontend/` (React). Move the Streamlit app to `legacy/streamlit_app/` and exclude it from Docker after parity is proven (U12). |
| D2 | Frontend: React 18, TypeScript (strict), Vite, Tailwind CSS, shadcn/ui (Radix), Apache ECharts (lazy-loaded), TanStack Query + TanStack Table (virtualized), Zustand, react-router, react-hook-form + zod, react-i18next, react-grid-layout (Should), Vitest, Testing Library, Playwright, axe-core. Self-host fonts (Inter via `@fontsource`), no external CDNs. |
| D3 | Backend: Python 3.12, FastAPI + Uvicorn (**1 worker**), SQLAlchemy 2 + Alembic, Pydantic v2, argon2-cffi, PyJWT, cryptography, rapidfuzz, scipy, httpx (tests). DB: SQLite locally, PostgreSQL via `DATABASE_URL` on Render. |
| D4 | Single origin: FastAPI serves the built frontend. No CORS. |
| D5 | One workspace per organization; each user belongs to one workspace; roles `admin`, `analyst`, `viewer`. |
| D6 | `STORAGE_MODE=ephemeral` (default: data lives in memory/temp and is deleted at logout/idle timeout) or `persistent` (datasets saved as encrypted Parquet under `DATA_DIR`). |
| D7 | Languages: `uz` (Uzbek, Latin script, **default**) and `en`. i18n keys, formatters and glossary are locale-driven so `ru` and `uz-Cyrl` can be added by adding files only. |
| D8 | Insight and answer text is produced from **human-reviewed message frames** (Section 5). Dynamic values (segment names, columns) are always placed in «guillemets» and never take Uzbek suffixes. LLM polishing is optional and **off by default**. |
| D9 | The LLM may be used only for: (a) Ask fallback understanding, (b) KPI suggestions in the restricted DSL, (c) domain-classification assist, (d) optional polish. It never produces numbers. |
| D10 | KPI Autopilot (Section 6): users never type KPI rules. AI-suggested metrics use a restricted JSON DSL compiled to SQL by code; the LLM never writes metric SQL. |
| D11 | Ask uses a deterministic **intent router** first (Uzbek/English patterns), the LLM path second. v2 candidate voting is retained for the LLM path. |
| D12 | Two reading levels: **Oddiy / Simple** (default) and **Mutaxassis / Expert** (shows SQL, statistics, technical terms). |
| D13 | Templates (Must): `retail`, `bank`, `sales`, `statistics`, `generic`. Templates (Should): `project`, `expenses`. |
| D14 | Cross-filtering by clicking chart elements is **Must**. Drag-resize tile layout is **Should**. |
| D15 | Theme: `light`, `dark`, `system` (default `system`); CSS variables; no flash of wrong theme on load. |
| D16 | Every chart has a "view as table" alternative (accessibility and trust). |
| D17 | Auth: argon2id, short-lived access JWT + rotating refresh token in `httpOnly; Secure; SameSite=Lax` cookies, double-submit CSRF token. |
| D18 | The v2 Egress Gate, audit log, privacy modes (`demo`, `cloud`, `local`), PII dropping, k-suppression and offline mode are kept and extended with per-workspace policy. |
| D19 | Long tasks (ingest, autopilot, insights) run as jobs: in-process thread pool + a DB `jobs` table; progress through Server-Sent Events. No Redis or Celery. |
| D20 | Workspace setting `currency` (default `UZS`, shown as «so'm»); other options `USD`, `EUR`, `RUB`. |
| D21 | Statistics use `scipy` with Benjamini–Hochberg correction across tests; effect size is required in addition to p-value. |
| D22 | Generated TypeScript API client from OpenAPI (`openapi-typescript`); CI check that it is up to date. |
| D23 | Shared format fixtures `shared/format_cases.json` are tested by **both** backend and frontend so numbers format identically everywhere. |

---

## 3. Target Architecture

```
Browser (React SPA, uz/en, light/dark)
   │  HTTPS, same origin, cookies + CSRF header
   ▼
FastAPI  (/api/v1 ...)  ── serves built SPA at /
   ├─ auth/ workspaces/ audit/            (SQLAlchemy: users, workspaces, sessions, audit_chain, jobs, datasets, dashboards)
   ├─ services/ (thin layer calling core) ── ingest · autopilot · dashboard · insights · ask
   └─ core/  (v2 engine, extended)        ── DuckDB per dataset, metrics, detectors, egress gate, llm gateway
        ▲
   Storage: ephemeral temp dir  |  encrypted Parquet in DATA_DIR (persistent mode)
```

### 3.1 Repository layout

```
finalyzer/
├── core/                      # v2 engine (kept) + new modules below
│   ├── autopilot/ {classifier.py, dsl.py, suggest.py, heuristics.py, scoring.py}
│   ├── metrics/packs/ {generic,retail,bank,sales,statistics,project,expenses}.yaml
│   ├── templates/ {dashboards/*.json}         # tile layouts per domain
│   ├── nlu/ {normalize.py, router.py, entities.py, followups.py}
│   ├── insights/ {detectors_sales.py, detectors_stats.py, frames.py, plain.py, polish.py}
│   └── i18n/ {formats.py, glossary_uz.json, glossary_en.json, frames_uz.json, frames_en.json, synonyms_uz_ru_en.json}
├── backend/
│   ├── app/ {main.py, config.py, deps.py, errors.py, security/, models/, schemas/, routers/, services/, jobs/, cli.py}
│   ├── alembic/  alembic.ini
│   └── tests/
├── frontend/
│   ├── src/ {app/, components/, charts/, features/{auth,datasets,dashboard,insights,ask,privacy,admin,settings}/, i18n/{uz.json,en.json}, theme/, lib/, api/ (generated)}
│   ├── e2e/  index.html  vite.config.ts  tailwind.config.ts  package.json
├── shared/format_cases.json
├── scripts/ {generate_demo_data.py (extended), eval_golden.py, eval_uzbek.py, perf_check.py, check_payloads.py, gen_api_client.sh}
├── data/demo/ {retail, bank, sales, statistics, (project)} + golden_questions.yaml + golden_questions_uz.yaml
├── legacy/streamlit_app/
├── docs/ {uz_review.md, USER_GUIDE_uz.md, USER_GUIDE_en.md, ADMIN_GUIDE.md, SECURITY.md, ARCHITECTURE.md}
├── Dockerfile  docker-compose.yml  render.yaml  .env.example  README.md  DEPLOY.md  KNOWN_LIMITATIONS.md  ASSUMPTIONS.md
└── tests/                     # v2 tests (kept green)
```

---

## 4. Security Architecture

Every row needs an automated test unless marked "doc".

| ID | Requirement | Implementation |
|---|---|---|
| S1 | Password storage | argon2id (`argon2-cffi` defaults tuned at startup), min length 12, rejected if in an embedded list of the 10,000 most common passwords |
| S2 | Brute-force protection | 5 failed logins → exponential lockout (1, 2, 4, … up to 30 min) per account and per IP; generic error text; constant-time comparisons |
| S3 | Sessions | Access JWT 15 min; refresh token 7 days, random 256-bit, **stored hashed**, rotated on every use, reuse detection revokes the family; logout and password change revoke all |
| S4 | Cookies | `httpOnly`, `Secure` (except localhost dev), `SameSite=Lax`, path-scoped refresh cookie |
| S5 | CSRF | Double-submit token (`X-CSRF-Token`) required for POST/PUT/PATCH/DELETE; Origin header check |
| S6 | Authorization | Role matrix (admin/analyst/viewer) in one `permissions.py`; every repository query scoped by `workspace_id`; **IDOR test generated from the OpenAPI route list**; a test asserts every route except the public allowlist requires auth |
| S7 | Rate limiting | Login 5/min; API 120/min/user; upload 10/hour/user; Ask 30/min/user; 429 with `Retry-After` |
| S8 | Security headers | CSP (`default-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'`, and `style-src-attr 'unsafe-inline'` only if charts require it), `X-Content-Type-Options`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, HSTS when HTTPS; a test fetches `/` and asserts all headers |
| S9 | Uploads | Extension and magic-byte check; XLSX must be a valid ZIP with `[Content_Types].xml`; zip-bomb guard (uncompressed size ≤ 20× and ≤ 500 MB); size/rows limits; random storage names; never execute formulas; filenames never used in paths |
| S10 | Encryption at rest | Persistent mode: AES-256-GCM per dataset with a random data key wrapped by `APP_MASTER_KEY` (32 bytes, base64, from env); key-rotation procedure documented; refuse to start in persistent mode without the key |
| S11 | Tamper-evident audit | Append-only `audit_chain` table; each record stores SHA-256 of the previous record; `/api/v1/admin/audit/verify` recomputes the chain; events: login, failed login, upload, delete, role change, privacy-mode change, egress call, export |
| S12 | LLM egress | v2 Egress Gate unchanged; workspace admin chooses privacy mode within `ALLOWED_PRIVACY_MODES`; UI "Privacy" page shows counters (bytes sent outside, blocked calls) and the exact payloads |
| S13 | Injection | Parameterized ORM queries; filter values validated and escaped (v2); metric DSL has no raw SQL; React renders text only (no `dangerouslySetInnerHTML`; lint rule enforces) |
| S14 | Prompt injection | Data values never enter prompts except allowed category values (D7 of v2); the model output is parsed into schemas, never executed except validated SQL |
| S15 | Secrets & config | All secrets from env; `.env` ignored; startup validation fails closed (missing `APP_SECRET_KEY`, weak keys) |
| S16 | Logging | Structured logs with request id; never log passwords, tokens, payloads, data values; a test greps logs from an integration run for a canary secret |
| S17 | Data lifecycle | Hard delete of dataset (files, DB rows, caches); idle sessions purge ephemeral data after `SESSION_IDLE_MINUTES` (default 30); admin retention setting for persistent data |
| S18 | Dependency hygiene | `pip-audit`, `npm audit --omit=dev`, `bandit`, `eslint-plugin-security` in a `scripts/security_check.sh`; results summarized in `docs/SECURITY.md` |
| S19 | Error hygiene | Problem-details JSON errors; no stack traces to clients; a correlation id shown to the user |
| S20 | Transport | Docs for TLS termination (Render provides it; Caddy for on-prem); app refuses insecure cookies in production mode |
| S21 | Legal drafts | `Terms` and `Privacy` pages exist as clearly marked drafts "to be reviewed by a lawyer" (doc) |
| S22 | Backups | Documented procedure only (doc) |

---

## 5. Uzbek-First Language System and Plain-Language Layer

### 5.1 i18n infrastructure
- Backend and frontend each have `uz.json` and `en.json` with identical key sets; tests fail on a missing or empty key, on unused keys, and on placeholder mismatches (`{name}` in one language but not the other).
- Language is chosen from: user profile → `localStorage` → `Accept-Language` → default `uz`. The switch (UZ | EN) is in the top bar and applies instantly to UI, charts, insights, Ask answers and exports. Insights and answers are generated in the requested language on demand (cache per language).
- **Pseudo-locale test:** a `pseudo` locale (every string +35% length with brackets) is used in a Playwright test to verify no truncation or layout breaks.

### 5.2 Uzbek orthography and input handling
- Display Uzbek Latin with the correct letters `oʻ`, `gʻ` (U+02BB) and `'` for the tutuq belgisi as in standard orthography; bundle a font that renders U+02BB (self-hosted Inter; add a rendering test string).
- `core/nlu/normalize.py`: normalize all apostrophe variants (`'`, `‘`, `’`, `ʻ`, `` ` ``) to one canonical form for matching; lowercase using Uzbek-safe rules; strip punctuation; collapse spaces. Users type `o'tgan oy`, `oʻtgan oy` or `o‘tgan oy`; all must match.

### 5.3 Number, date and currency formatting (one implementation per side, shared fixtures)
| Item | Uzbek (`uz`) | English (`en`) |
|---|---|---|
| Integer | `1 234 567` (narrow no-break space) | `1,234,567` |
| Decimal | `1 234,56` | `1,234.56` |
| Percent | `8,2%` (spoken as "foiz") | `8.2%` |
| Compact | `5 ming`, `12,8 mln`, `1,2 mlrd` | `5K`, `12.8M`, `1.2B` |
| Currency UZS | `12,8 mln so'm` | `UZS 12.8M` |
| Month names | yanvar, fevral, mart, aprel, may, iyun, iyul, avgust, sentabr, oktabr, noyabr, dekabr | standard |
| Weekdays | dushanba, seshanba, chorshanba, payshanba, juma, shanba, yakshanba | standard |
| Date | `2026-yil 12-avgust`; month label `2026-yil sentabr`; short `12.08.2026` | `12 Aug 2026` |
`shared/format_cases.json` holds ≥ 80 cases (value, locale, unit, expected string). Backend and frontend tests both load it.

### 5.4 Message frames (text without free-form generation)
`frames_uz.json` / `frames_en.json` contain one frame per (finding type × direction × reading level). Rules:
1. Dynamic values (segment names, column names, dates) appear only as `«{segment}»` or after a colon; **never** attach Uzbek suffixes to them. Rewrite sentences to avoid it.
2. Sentences ≤ 18 words; one idea per sentence; active voice; everyday words; no unexplained jargon (5.5).
3. Each card has: **Headline** (what happened), **Meaning** (why it matters, one sentence), **Next step** (one concrete action), and optional **Details** (Expert level).
4. Direction words come from a closed set so polarity can be verified: up = `oshdi, ko'paydi, o'sdi, yaxshilandi`; down = `kamaydi, pasaydi, tushdi, yomonlashdi`.
5. Frames carry a `reviewed: false/true` flag; the app shows nothing different, but `docs/uz_review.md` lists all `reviewed:false` frames for the owner.

Example frames (agent must implement these and write the rest in the same style; owner reviews):
- `trend_down` uz: `«{metric}» {compare} {pct} foizga kamaydi.` · en: `"{metric}" fell {pct}% {compare}.` with `{compare}` = `o'tgan oyga nisbatan` / `compared with last month`.
- `driver_negative` uz: `Pasayishning {share} foizi «{segment}» hissasiga to'g'ri keladi.`
- `concentration` uz: `{metric_short} ning {share} foizi bitta guruhdan keladi: «{segment}». Bu guruhda muammo chiqsa, umumiy natija sezilarli tushadi.` (use the pack's pre-written genitive form `metric_gen` so no suffix is built at runtime)
- `anomaly` uz: `{date} kuni «{metric}» odatdagidan keskin farq qildi: {value} (odatda taxminan {typical}).`
- `churn_up` uz: `O'tgan oyda oldingi oy mijozlarining {rate} foizi qaytib kelmadi (avvalgi oylarda o'rtacha {baseline} foiz).`
- `data_quality` uz: `«{column}» ustunida ma'lumotlarning {pct} foizi yo'q. Bu tahlil aniqligini pasaytirishi mumkin.`
- `segment_gap` uz: `«{best}» va «{worst}» o'rtasida katta farq bor: «{metric}» birinchisida {ratio} baravar yuqori.`
- `next_step_driver` uz: `Avval «{segment}» da nima o'zgarganini tekshiring.`
Metric packs therefore carry per-language fields: `label`, `label_gen` (genitive/linking form where needed), `short`, `synonyms`, `description_plain`.

### 5.5 Plain-language rules and glossary
- `glossary_uz.json` (≥ 150 entries, agent drafts, owner reviews) maps technical terms to a plain explanation, for example: churn → «mijozlar ketishi: oldin xarid qilgan, lekin endi qilmayotgan mijozlar»; anomaly → «odatdagidan keskin farq»; concentration → «bir narsaga haddan tashqari bog'liqlik»; correlation → «ikki ko'rsatkichning birga o'zgarishi»; median → «o'rtadagi qiymat»; standard deviation → «qiymatlar qanchalik tarqoq»; margin → «foydaning savdodagi ulushi»; win rate → «yutib olingan bitimlar ulushi».
- **Jargon lint:** a banned-term list (including the English and Uzbek forms of the terms above, `p-value`, `dispersiya`, `HHI`, `z-score`) must not appear in Simple-level text unless wrapped as a glossary token. Wrapped tokens render as dotted-underline hover/tap tooltips with the plain explanation. Test: generate cards for all demo datasets in both languages and assert zero unwrapped banned terms.
- Statistics wording: never show p-values at Simple level. Use a confidence label (`Ishonch: yuqori/o'rta/past`) and a plain sentence; Expert level shows test name, statistic, p-value, effect size.
- "Explain simpler" button on every insight and answer: switches that item to an even shorter frame variant (≤ 12 words per sentence, no numbers except the main one).
- Every answer states what data and period it covers, and carries the badge `Tasdiqlangan` (verified) or `Tasdiqlanmagan` (unverified, with a one-line reason).

### 5.6 Where the LLM may touch Uzbek text
Default pipeline is fully deterministic (frames). Optional `UZ_POLISH=true` asks the LLM to rephrase a frame-built paragraph more naturally. A polished text is accepted only if: (1) number-grounding passes (parser understands `1 234,56`, `12,8 mln`, `8,2%`); (2) polarity check passes (direction words match the finding); (3) all «quoted» entities are preserved; (4) jargon lint passes; (5) length ≤ 120% of the frame text. Otherwise use the frame text. Badge "AI-polished" appears only on accepted polished text. Keep the feature off until `scripts/eval_uzbek.py` shows it improves a human-rated sample; record the decision in `ASSUMPTIONS.md`.

### 5.7 Role and column synonyms
`synonyms_uz_ru_en.json` extends v2 role synonyms with Uzbek and Russian words (Appendix C). Matching normalizes apostrophes and transliteration variants (`sana/date/дата`, `summa/сумма`).

---

## 6. KPI Autopilot and Domain Templates

### 6.1 Pipeline (runs automatically after "Build")
1. **Domain classification** (`core/autopilot/classifier.py`): score every pack from role coverage, column-name synonyms (uz/ru/en), value hints (e.g. `won/lost`, `completed/failed`, `paid/refunded`), and data shape. Pick the best pack above a threshold. If there is no date/amount but many numeric columns → `statistics`. Else `generic`. Optional LLM assist (schema only, per privacy mode) breaks ties; offline uses scores only. Result carries a confidence and a plain-language explanation, shown to the user as «Biz ma'lumotingizni "Savdo" turiga o'xshatdik» with a one-click change.
2. **Pack metrics:** all pack metrics whose roles and values are satisfied are enabled.
3. **AI-suggested metrics (restricted DSL)** (`suggest.py`): the LLM (schema + profile only, per privacy mode) proposes ≤ 8 extra metrics as JSON. Code compiles them. **No raw SQL from the LLM.**
```json
{"id":"avg_cycle_days","agg":"avg_days_between","columns":["created_date","close_date"],
 "filter":[{"column":"status","op":"=","value":"won"}],
 "unit":"days","good_direction":"down",
 "label":{"en":"Average sales cycle","uz":"O'rtacha sotuv davri"}}
```
   Allowed `agg`: `sum, avg, count, count_distinct, min, max, median, stddev, quantile(q), avg_days_between, ratio` (ratio = numerator DSL ÷ denominator DSL, optional ×100). Allowed filter ops: `=, !=, in, >, >=, <, <=, is_null, not_null`; values validated against known categories.
4. **Validation (every metric, pack or AI):** compiles; executes within the timeout; returns a finite number; is not constant; not >50% null; not an ID-like column aggregation (sum of IDs forbidden); ratio denominators nonzero; parity with an independent pandas computation within 1e-6 (always possible because the DSL is restricted); deduplicated by canonical form against existing metrics. Failures are dropped silently and logged.
5. **Uzbek labels:** AI proposes `en` and `uz` labels; if a term exists in the glossary, the glossary label wins; otherwise the label is marked `ai_translated` and listed in the KPI manager for optional review (never blocks use).
6. **KPI selection:** score = pack priority + variability + coverage + validation score; show the top 6–8 as KPI tiles (primary first). Everything else sits in the KPI manager.
7. **Offline / no-key mode:** `heuristics.py` generates DSL candidates deterministically: sum/avg of non-ID numeric columns, share of each status-like value, average duration between date pairs, distinct counts of ID roles, missing rates. Same validation applies.
8. **KPI manager (optional UI):** toggle, rename, reorder, delete; shows definition in plain words and an Expert view with the DSL and SQL; users still never *have* to enter rules.

### 6.2 Domain packs (Must unless stated)
Each pack YAML includes: `id`, `labels` (uz/en), `roles`, `metrics` (with `label`, `label_gen`, `short`, `synonyms`, `description_plain`, `unit`, `good_direction`, `additive`, `priority`), `dimensions_priority`, `detectors` (which apply), `dashboard_template`, `suggested_questions` (uz/en), `demo_dataset`.
- **retail**, **bank**: keep v2 metrics; add Uzbek fields; add bank `avg_ticket`, `failed_rate_pct`, `fee_income`.
- **sales** (CRM pipeline): `pipeline_value` (open amount), `won_revenue`, `deals_won`, `deals_lost`, `win_rate_pct`, `avg_deal_size`, `avg_cycle_days`, `new_deals`, `stalled_deals` (open > 90 days), `quota_attainment_pct` (only if a `target` role exists). Roles add `stage`, `owner`, `source`, `target`.
- **statistics**: `row_count`, per numeric column `mean, median, std, min, max, p25, p75, missing_pct` (autopilot picks the most informative columns), `pass_rate_pct` when a boolean/status success column exists.
- **generic**: row count, sum/avg of the main amount, distinct IDs.
- **project** (Should; matches the reference screenshot): `total_projects`, `completed`, `in_progress`, `on_hold`, `total_budget`, `actual_cost`, `cost_variance`, `tasks_completed_pct`, `deadline_met_pct`.
- **expenses** (Should): `total_spend`, `avg_expense`, `spend_by_category_share`, `largest_category_share`.

### 6.3 Dashboard templates (`core/templates/dashboards/*.json`)
A template is a 12-column grid of tiles; each tile: `id, type, title_key, metric_ids | metric_role, dimension (role|"auto"), size {w,h}, requires, cross_filter (bool), drill [...]`. If requirements are unmet the tile is dropped and the grid reflows (never empty boxes). Chart types: `kpi, big_number, donut, line, area, bar_h, bar_v, stacked_bar, gauge, bullet, funnel, heatmap, treemap, scatter, histogram, boxplot, waterfall, table`. Layouts per domain are in Appendix A. Dimension `"auto"` picks the most informative low-cardinality dimension (highest normalized entropy with 3–12 categories).

---

## 7. Ask v2 (Uzbek-first questions)

1. **Normalize** (5.2) and detect language (`uz|en|ru` via script + keyword scoring; Russian is understood best-effort, answered in the UI language).
2. **Intent router** (`core/nlu/router.py`): ≥ 30 parametrized intents with Uzbek and English patterns and slots, for example: total of a metric; metric by dimension; top/bottom N; trend over time; compare periods (`o'tgan oyga nisbatan`, `bu yil o'tgan yilga qarshi`); share of total; average/min/max; count/distinct; filter by value; best/worst performer; "why did X drop" (maps to driver analysis); distribution (statistics); correlation between two columns (statistics); funnel conversion (sales). Entity resolution uses `rapidfuzz` over metric labels, synonyms, dimension names and category values (threshold 85, ambiguity → clarification buttons).
3. If the router is confident (all slots resolved), **compile SQL deterministically** (no LLM). `verified=True` because the path is deterministic and the SQL passes the v2 validator.
4. Otherwise use the **LLM path** from v2 (candidates + voting + repair), now with an English-normalized restatement and the bilingual glossary in the prompt. Unknown/ambiguous → short Uzbek clarification with 2–4 buttons. Off-topic or unsafe → polite refusal in Uzbek.
5. **Answer composition:** `PlainAnswer {headline, explanation, so_what, next_step, details(Expert), badge, used_filters, period, chart, table}` built from frames. In `cloud` mode no result rows go to an LLM (v2 D8).
6. **Follow-ups:** 3 suggested next questions derived from the result (deterministic).
7. **Conversation context (Should):** a follow-up like `shu filial bo'yicha-chi?` inherits the previous question's metric and period.
8. Streaming: the API streams stages `understanding → computing → writing → done` (SSE) so the UI shows progress.
9. **Evaluation:** `data/demo/golden_questions_uz.yaml` with 60 Uzbek questions (15 per dataset: retail, bank, sales, statistics), 20 with English parallels, expected SQL, and a flag for router-expected vs LLM-expected. `scripts/eval_uzbek.py` reports router coverage, router accuracy, LLM-path accuracy, clarification rate. Targets (measured, not promised): router accuracy ≥ 95% on router-expected questions; overall ≥ 85% with a live cloud model; report real numbers.

---

## 8. Insights v2

Keep all v2 detectors (trend, anomaly, concentration, driver, segment_gap, segment_momentum, churn, data_quality) and add:

| Detector | Domain | Rule |
|---|---|---|
| `funnel_drop` | sales | Stage conversion between consecutive ordered stages; flag the stage pair whose conversion is ≥ 20 pp below the median of the others and has ≥ 100 deals entering |
| `stalled` | sales | Open deals older than 90 days (configurable) ≥ 15% of open pipeline value |
| `win_rate_gap` | sales | win-rate by owner/source/product: best vs worst ratio ≥ 2 with support ≥ 30 closed deals each (reuses segment_gap machinery) |
| `cycle_change` | sales | average cycle days last 3 months vs prior 3 months, ≥ 15% change |
| `outliers` | statistics | IQR rule (1.5×) on each numeric column; flag when 0.2% ≤ share ≤ 10% or any value beyond 3×IQR; report counts and extreme values |
| `correlation` | statistics | Spearman ρ and Pearson r for numeric pairs, n ≥ 30, \|ρ\| ≥ 0.5 and BH-adjusted p < 0.01; top 3 pairs; always add the "correlation is not proof of cause" caution |
| `group_difference` | statistics | Welch t-test (2 groups) or one-way ANOVA (≥ 3) for numeric by categorical (≤ 12 groups, each n ≥ 20); flag when BH-adjusted p < 0.01 **and** effect size ≥ medium (Cohen's d ≥ 0.5 or η² ≥ 0.06) |
| `skewness` | statistics | \|skew\| ≥ 1 on columns with n ≥ 50; plain message "most values are small but a few are very large" |
| `missing_pattern` | statistics | missingness of a column differs strongly by category (chi-square, BH-adjusted p < 0.01, Cramér's V ≥ 0.2) |
| `trend_regression` | statistics | numeric column vs date: linear slope with p < 0.01 and R² ≥ 0.05; plain message about direction and size per month |

- Ranking, suppression (k ≥ 5 in `cloud`), template-narrator fallback, number-grounding and "Next steps" from v2 remain. Grounding is upgraded to parse Uzbek/English formatted numbers and to verify polarity words (5.6).
- Cards are produced in the requested language and level from frames. The Insights page shows: priority color, headline, meaning, next step, evidence chips (metric, period, segment), "How we know" (Expert: detector, numbers, test statistics), glossary tooltips, "Explain simpler", status (`yangi / ko'rildi / bajarildi`; persisted per user in persistent mode).
- **False-positive guard:** on a noise-only dataset per domain, ≤ 1 finding.

---

## 9. Frontend UX Specification

### 9.1 Visual language (from the reference screenshot)
- Dark theme default look: very dark navy-charcoal background, slightly lighter rounded cards (radius 16 px), subtle borders, vivid saturated KPI tiles (solid green, blue, amber, teal, red) with a large number and small caps label, donuts with right-side legends, smooth teal line charts with faint area, bullet/actual-vs-target bars, gauges for percentage targets, stacked horizontal bars. Dense but airy: 12-column grid, 16–20 px gaps.
- **Design tokens** (CSS variables; Tailwind reads them):

| Token | Dark | Light |
|---|---|---|
| `--bg` | `#0E141B` | `#F4F6F9` |
| `--surface` | `#161E27` | `#FFFFFF` |
| `--surface-2` | `#1D2733` | `#EEF1F5` |
| `--border` | `#273341` | `#D9DFE7` |
| `--text` | `#E6EDF3` | `#0F1720` |
| `--muted` | `#8B98A5` | `#5B6776` |
| `--green` | `#2EAD6B` | `#1F8F55` |
| `--blue` | `#2F80ED` | `#1F6BD6` |
| `--amber` | `#F2A93B` | `#C98312` |
| `--teal` | `#1FA3A3` | `#13878A` |
| `--red` | `#E5484D` | `#C93238` |
| `--purple` | `#8E6CEF` | `#6D4BD1` |
Chart palette order: blue, amber, green, red, teal, purple, then tints. A script `scripts/check_contrast.py` verifies WCAG AA (4.5:1) for text/background token pairs in both themes and fails the build otherwise.
- Typography: Inter (self-hosted), numerals tabular, KPI numbers 32–40 px semibold, labels 12 px uppercase tracking. Motion 150–250 ms, respects `prefers-reduced-motion`.
- **Theme switch:** sun/moon/auto control in the top bar; stored per user (and `localStorage` before login); an inline script in `index.html` sets `data-theme` before paint (no flash). ECharts themes are generated from the tokens and update live on switch.
- **Do not** reproduce the screenshot's text, logos, watermark, or the Power BI mark.

### 9.2 App shell and pages
Left navigation (collapsible; bottom tab bar on mobile), top bar with: dataset selector, global filter toggle, **language switch UZ | EN**, **theme switch**, user menu.
| Route | Content |
|---|---|
| `/login`, `/first-run` | Sign in; first-run admin creation (only when no users) |
| `/` | Home: recent datasets, "Try a demo" cards per template, quick tips |
| `/datasets` | List + upload wizard (9.3) |
| `/datasets/:id/dashboard` | Auto dashboard (9.4) |
| `/datasets/:id/insights` | Insight cards, "Next steps" |
| `/datasets/:id/ask` | Chat-style question box |
| `/datasets/:id/kpis` | KPI manager |
| `/privacy` | Egress counters, payload viewer, mode badge |
| `/settings` | Profile, language, theme, reading level, currency |
| `/admin` | Users and roles, privacy policy, retention, audit chain verify |

### 9.3 Upload wizard (≤ 5 clicks to dashboard)
1. Drop files (CSV/XLSX) or pick a demo. 2. Progress with live job events. 3. **Summary screen:** "What we found" (tables, rows), cleaning report in plain words, "Personal data removed from analysis" list, detected domain (changeable), detected fact table and relationships (pre-confirmed when containment ≥ 0.95; editable behind "Advanced"), detected column roles (editable behind "Advanced"). 4. Button **Build dashboard**. 5. Autopilot runs (progress), then redirect. Nothing requires typing KPI rules.

### 9.4 Dashboard behavior
- **Filter bar** (sticky): date range with presets (Bu oy, O'tgan oy, Oxirgi 30 kun, Bu yil, Hammasi), up to 4 auto-chosen multi-selects, numeric range, active-filter chips, "Tozalash". Changes debounce (250 ms), cancel in-flight requests (`AbortController`).
- **Cross-filter:** clicking a donut slice, bar, funnel stage, or heatmap cell adds a chip; clicking again removes it. All tiles refresh. Line chart brush selects a date range.
- **Drill-down:** month → day when available; double-click or tile menu.
- **KPI tile:** value, label, delta chip vs previous period (green/red by `good_direction`, grey if neutral), optional sparkline; solid-color variant for the top row and outlined variant elsewhere; skeleton while loading; "n/a" with tooltip when no previous period.
- **Tile menu:** Expand, View as table, "Qanday hisoblandi?" (plain explanation; Expert shows SQL/metric DSL), Export PNG, Export CSV.
- **Layout editing (Should):** drag/resize with `react-grid-layout`; save per dataset; "Reset to template".
- **Export:** page to PNG and print-friendly PDF via the browser print stylesheet; CSV per tile.
- Tiles lazy-load when they enter the viewport; table tiles are virtualized; error tiles show a retry button and a plain-language reason.
- **Responsive:** ≥ 1280 px full grid; 768–1279 two columns; < 768 single column with KPI tiles in a horizontal scroller.
- **Accessibility:** keyboard-reachable tiles and menus, visible focus rings, `aria-label` on every chart plus the "view as table" alternative, color never the only signal (icons and arrows for up/down), axe-core checks on 4 key pages with zero serious violations.

### 9.5 Insights and Ask pages
- **Insights:** cards sorted by priority; color rail (red high, amber medium, blue low); chips; tooltip glossary; "Explain simpler"; "Expert details" toggle (global reading level also in settings); a banner when text is template-written vs AI-polished.
- **Ask:** chat layout; input with suggested question chips (from the pack, in the active language); streaming stage indicator; answer card with headline, explanation, next step, chart/table, `Tasdiqlangan/Tasdiqlanmagan` badge, "Qanday hisoblandi?" drawer, follow-up chips; clarification buttons; empty state with examples; error state in plain Uzbek with retry.

### 9.6 Performance budgets
Initial JS ≤ 350 KB gzipped (charts and heavy features lazy-loaded); route transitions < 150 ms after data is cached; dashboard first meaningful tiles ≤ 2.5 s on the demo dataset locally; tile query ≤ 2 s at 1M rows (backend); API p95 ≤ 500 ms for non-compute endpoints.

---

## 10. API (all under `/api/v1`, OpenAPI documented, problem-details errors)

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/first-admin`, `/auth/login`, `/auth/logout`, `/auth/refresh`, `GET /auth/me`, `POST /auth/change-password`, `GET /auth/csrf` |
| Admin | `GET/POST/PATCH/DELETE /admin/users`, `GET/PUT /admin/policy`, `GET /admin/audit`, `GET /admin/audit/verify` |
| Datasets | `POST /datasets` (multipart), `GET /datasets`, `GET /datasets/{id}`, `DELETE /datasets/{id}`, `GET /datasets/{id}/profile`, `PATCH /datasets/{id}/model` (fact table, relationships, roles, domain), `POST /datasets/{id}/build` |
| Demo | `GET /demo`, `POST /demo/{name}/load` |
| Jobs | `GET /jobs/{id}`, `GET /jobs/{id}/events` (SSE) |
| KPIs | `GET /datasets/{id}/kpis`, `PATCH /datasets/{id}/kpis/{metric_id}` (enable, rename, order) |
| Dashboard | `GET /datasets/{id}/dashboard?lang=` (resolved template with tile specs), `POST /datasets/{id}/tiles/query` (tile id + filters → data), `GET/PUT /datasets/{id}/layout` |
| Insights | `GET /datasets/{id}/insights?lang=&level=`, `PATCH /insights/{id}/status` |
| Ask | `POST /datasets/{id}/ask` (SSE), `GET /datasets/{id}/suggested-questions?lang=` |
| Privacy | `GET /privacy/summary`, `GET /privacy/events` |
| Meta | `GET /healthz`, `GET /readyz`, `GET /version` |

Tile query response is neutral JSON (series, categories, values, units, `formatted` strings produced with the shared formatter, `source_sql` hidden unless Expert + analyst/admin role). Viewer role: read-only (dashboards, insights, Ask); analyst: upload, build, KPI edits; admin: everything.

---

## 11. Phases

### U0 — Baseline audit and freeze
Run all v2 tests; record versions and results in `docs/ARCHITECTURE.md`; tag the commit; create `legacy/` plan; check that `core/` has no Streamlit imports (fix if any, with tests). **Accept:** v2 tests green; baseline doc exists.

### U1 — Backend foundation
FastAPI app, settings (fail-closed validation), SQLAlchemy models + Alembic migration, request-id logging, problem-details errors, health endpoints, security-header middleware, rate limiter, static SPA serving (placeholder page). **Accept:** `GET /healthz` works; header test passes; migration upgrades/downgrades on SQLite; log canary test passes; OpenAPI generated.

### U2 — Auth, workspaces, roles, audit chain
S1–S7, S11, S15, S16, S19. CLI `python -m app.cli create-admin`. **Accept:** auth flow tests (login, refresh rotation, reuse detection, lockout, password policy); CSRF tests; role-matrix tests; IDOR test auto-generated from OpenAPI routes; audit-chain verify detects a tampered row; every non-public route rejects anonymous calls.

### U3 — Dataset service, jobs, storage
Upload pipeline (S9) calling v2 ingestion, jobs + SSE, DuckDB manager (LRU, memory cap), ephemeral and persistent storage with encryption (S10), deletion and idle purge (S17), demo loader. **Accept:** upload of valid/invalid/zip-bomb/renamed files behaves correctly; persistent mode round-trip (encrypt → restart → decrypt) works and ciphertext on disk contains no plaintext canary; delete leaves no files; two workspaces cannot access each other's datasets.

### U4 — Uzbek language layer (backend)
`core/i18n/` formatters, normalization, glossary, frames skeleton, synonyms; `shared/format_cases.json` (≥ 80 cases); i18n key-parity tests; `docs/uz_review.md` generator listing every unreviewed Uzbek string. **Accept:** formatter tests pass; apostrophe-variant tests pass; key parity and placeholder parity pass; glossary ≥ 150 entries; frames exist for every finding type × direction × level in both languages.

### U5 — KPI Autopilot and templates
Classifier, DSL, heuristics, suggest, validation, scoring; packs for all domains; dashboard templates; extend `generate_demo_data.py` with **sales** and **statistics** datasets (Appendix B) and optional project dataset. **Accept:** classifier ≥ 11/12 on 12 fixture files with varied uz/ru/en column names; for an unseen-domain fixture (HR attrition) autopilot yields ≥ 4 valid metrics with no user input, none failing validation; DSL fuzz test (200 random/invalid DSL objects) never produces unsafe SQL or an exception; metric parity for all packs on all demo datasets; autopilot ≤ 5 s offline and ≤ 20 s live on demo data; offline mode needs no key.

### U6 — Insights v2 and plain language
New detectors, frames-based narrator, level handling, glossary tokens, upgraded grounding (Uzbek/English number parsing, polarity), optional polish module (default off), Next steps in both languages. **Accept:** planted patterns detected ≥ 7/8 for each of retail, bank, sales, and statistics (statistics includes one negative control that must **not** be flagged); noise dataset ≤ 1 finding; jargon lint zero violations across all demo datasets in both languages and both levels; grounding tests with `1 234,56`, `12,8 mln`, `8,2%`, `12.8M`; polarity test rejects a polished text that flips direction; `cloud`-mode payload test shows no sub-5 groups.

### U7 — Ask v2
Normalize, router, entity resolution, deterministic SQL compiler, LLM fallback integration, `PlainAnswer`, follow-ups, SSE, Uzbek golden set (60) and eval script. **Accept:** offline, all router-expected golden questions return correct results (result-set equality with expected SQL); 40 v2 English golden tests still pass; clarification appears for ambiguous inputs (test with 10 ambiguous phrases); unsafe/off-topic prompts refused in Uzbek (test with 10); router never produces SQL outside the validator's allowance.

### U8 — Frontend foundation
Vite project, Tailwind with tokens, theme system (no flash), i18n (uz/en/pseudo), shell, routing, generated API client with CSRF handling, auth screens, error boundaries, toasts, base components (Button, Card, Tooltip with glossary, Dialog, Tabs, Select, Skeletons, EmptyState). **Accept:** `npm run typecheck`, `lint`, unit tests pass; theme toggle persists and has no flash (Playwright screenshot before first paint); language switch changes all visible strings; contrast script passes both themes; pseudo-locale layout test passes on shell pages; initial JS budget met.

### U9 — Upload wizard and dataset flow
Wizard (9.3), job progress via SSE, summary screen, domain override, advanced editors, demo cards. **Accept:** Playwright: new admin → first run → load demo → dashboard in ≤ 5 clicks, in `uz` and `en`; invalid file shows a plain-language error; PII removal list shown.

### U10 — Dashboard UI
Tile renderer, ECharts wrappers (donut, line/area, bars, stacked, gauge, bullet, funnel, heatmap, treemap, scatter, histogram, boxplot, waterfall, table), KPI tiles, filter bar, cross-filter, drill, tile menu, view-as-table, export, responsive grid, templates from Appendix A, layout editing (Should). **Accept:** for each of the 4 core demo datasets the auto dashboard renders ≥ 8 tiles with no empty tile; cross-filter test (click donut slice → chips + all tiles refresh with numbers equal to backend recomputation); every chart has a table alternative; axe: zero serious violations on dashboard in both themes; visual snapshot tests exist for dark and light.

### U11 — Insights, KPI manager, Ask UI, Privacy and Admin UI
**Accept:** Playwright flows: read an insight → "Explain simpler" changes text; ask a suggested question in Uzbek and get a verified answer with chart; clarification buttons work; privacy page shows `0` bytes in offline mode and exact payloads in a mocked live mode; admin can create a viewer; viewer cannot upload (UI hidden and API 403).

### U12 — Hardening and parity
Security test suite (S1–S20 mapped to tests), `scripts/security_check.sh`, chaos tests (LLM down, slow, malformed; DB busy; large result), `perf_check.py` for 1M rows, E2E on both themes and languages, Streamlit parity checklist then removal from Docker. **Accept:** all suites green; security check output stored; perf numbers recorded; `legacy/` excluded from the image.

### U13 — Packaging and deployment
Multi-stage Dockerfile (Node build → Python runtime, non-root, 1 Uvicorn worker, reads `$PORT`), `docker-compose.yml` (app + Postgres, optional Ollama profile, Caddy TLS example for on-prem), `render.yaml` (web service from Docker; optional Postgres; env vars; health check `/healthz`), `.env.example`, `README.md`, `DEPLOY.md`, `docs/USER_GUIDE_uz.md`, `USER_GUIDE_en.md`, `ADMIN_GUIDE.md`, `SECURITY.md`, `KNOWN_LIMITATIONS.md`. **Accept:** `docker compose up` yields a working app on a clean machine; smoke test passes (first-admin → demo → dashboard → insights → Ask offline); measured memory (idle/peak) written in `DEPLOY.md` with a plan-sizing warning (peak × 1.3); no secrets in the image or repo (secret-scan output stored).

---

## 12. Quality Gates (always on)

| Gate | Threshold |
|---|---|
| v2 `tests/` | 100% green |
| Backend tests | green; coverage reported (target ≥ 80% on `backend/app/security` and `core/autopilot`, `core/nlu`, `core/insights`) |
| Frontend unit + E2E | green in `uz`/`en` × `dark`/`light` for critical flows |
| Metric parity (all packs, all demo datasets) | 100% within 1e-6 |
| Planted patterns | ≥ 7/8 per core dataset; negative control not flagged |
| Jargon lint, i18n parity, format cases | 0 violations |
| Security | no critical/high from audits unaddressed; all S-rows tested |
| Accessibility | axe zero serious violations on 4 key pages, both themes |
| Contrast | WCAG AA for token pairs |
| Performance | budgets in 9.6 recorded |
| Uzbek Ask eval | numbers reported honestly in `docs/` |

---

## 13. Deployment Targets

- **Local:** Python venv + `npm run dev` (Vite proxy to FastAPI) or `docker compose up`.
- **Render (demo/SME):** one Docker web service (serves API + SPA), `STORAGE_MODE=ephemeral` by default, `PRIVACY_MODE=demo`, optional Render Postgres for accounts; secrets set in the dashboard. State clearly in `DEPLOY.md`: ephemeral disk means uploaded datasets disappear on redeploy or sleep; a persistent disk is required for `persistent` mode. Verify Render plan RAM against the measured peak; verify current Render limits before choosing a plan.
- **On-premise (banks):** `docker-compose.yml` with Postgres, `PRIVACY_MODE=local`, local LLM endpoint, TLS reverse proxy; all traffic stays inside the customer's network.

---

## 14. Human-in-the-Loop Checklist (things only the owner can do)

1. Review every Uzbek frame, glossary entry and UI string listed in `docs/uz_review.md`; mark `reviewed: true`.
2. Run `scripts/eval_golden.py` and `scripts/eval_uzbek.py` with the real model; keep the reports; use only measured numbers in pitches.
3. Provide `LLM_MODEL`, `LLM_API_KEY`, `APP_SECRET_KEY`, `APP_MASTER_KEY` (generate with the command in `DEPLOY.md`).
4. Have a lawyer review the draft Terms/Privacy before real customers; verify data-residency requirements before bank pilots.
5. Test with 3–5 real users (a shop owner, a bank employee) and add their real questions to the Uzbek golden set.

---

## Appendix A — Dashboard templates (tile lists, 12-column grid)

**Common top row (all templates):** up to 6–8 KPI tiles from the autopilot (solid colors green, blue, amber, teal, red, purple), each with delta chip and sparkline.

**retail:** trend line (primary metric, previous-year overlay) w8; donut (revenue share by category) w4; bar_h (top 10 products) w6; stacked_bar (revenue by channel by month) w6; gauge (gross margin %) w3; bullet (revenue by store vs average) w5; heatmap (weekday × hour or weekday × week) w4; table (top customers by spend, PII-free) w12.

**bank:** trend line (volume) w8; donut (volume by channel) w4; bar_v (volume by branch) w6; gauge (failed rate %, inverse good) w3; stacked_bar (completed/failed/reversed by month) w6; line (active customers) w6; heatmap (transaction type × weekday) w6; table (largest branches) w12.

**sales:** funnel (stage conversion) w4; big_number (pipeline value) w2 + gauge (win rate %) w3; line (won revenue by month) w7; bar_h (win rate by owner) w6; donut (deals by source) w3; bullet (quota attainment by owner, only if target exists, else average deal size by owner) w6; histogram (deal size) w4; table (stalled deals, top 10) w12.

**statistics:** KPI row (count, mean, median, std, missing %, pass rate) ; histogram + boxplot of the most informative numeric column w6 each; correlation heatmap w6; scatter (top correlated pair, with trend line) w6; bar_v (group means with whiskers for the most significant grouping) w6; line (mean over time with slope) w6; table (descriptive statistics for all numeric columns) w12.

**generic:** KPI row; trend line; donut (auto dimension); bar_h (top 10); table.

**project (Should, mirrors the reference):** KPI row (total, completed, in progress, on hold, total budget, tasks completed); donut (status); line (cost variance by month); bullet (actual vs budget by project); gauge (deadline met %); bar_h (priority); bar_v (projects by manager); stacked bar (tasks by status).

---

## Appendix B — New demo datasets (generated deterministically, seed 42, 2024-10-01 → 2026-09-30)

**sales** (about 20k deals): `deals(deal_id, created_date, close_date, owner_id, region, source[website|referral|cold_call|event|partner], product[Basic|Pro|Enterprise], stage[Lead|Qualified|Proposal|Negotiation|Won|Lost], status[open|won|lost], amount, lost_reason)`, `owners(owner_id, owner_name["Owner 01".."Owner 12"], team)`. Fact `deals`.
Planted patterns: **S1** win rate falls from ~31% to ~22% in 2026-09 (trend, percent). **S2** Owner 05 win rate about half of the best owner (win_rate_gap). **S3** `referral` win rate ≈ 3× `cold_call` (win_rate_gap on source). **S4** `Enterprise` ≈ 55% of won revenue (concentration). **S5** Proposal→Negotiation conversion ≈ 35% vs ≈ 70% elsewhere (funnel_drop). **S6** ≈ 18% of open pipeline value older than 90 days (stalled). **S7** average cycle lengthens ≈ 20% over the last 3 months (cycle_change). **S8** `region` 30% null (data_quality).

**statistics** (manufacturing quality, about 30k rows): `measurements(sample_id, measured_at, line[A|B|C], shift[day|night], operator, temperature, pressure, thickness_mm, defects, passed[yes|no])`.
Planted patterns: **T1** ≈ 0.5% extreme outliers in `thickness_mm` (outliers). **T2** `temperature` ↔ `defects` Spearman ≈ 0.7 (correlation). **T3** line C mean `thickness_mm` differs with a large effect (group_difference). **T4** night shift defect rate ≈ 2× day (group_difference on a rate/`defects`). **T5** `defects` strongly right-skewed (skewness). **T6** `pressure` 15% missing, concentrated on line B (missing_pattern). **T7** `temperature` rises steadily over time with a significant slope (trend_regression). **T8 negative control:** `pressure` and `thickness_mm` unrelated; **no** correlation finding may be produced for this pair.

**project** (Should, about 60 projects, 600 tasks) following the project pack roles.

---

## Appendix C — Role synonyms (extend v2; normalized, apostrophes unified)

| Role | Uzbek | Russian | English (v2) |
|---|---|---|---|
| date | sana, vaqt, sanasi, yaratilgan_sana, sotuv_sanasi | дата, дата_продажи | date, order_date, sale_date, txn_date |
| amount | summa, jami, tushum, savdo, narx_jami, qiymat | сумма, выручка, итого | amount, revenue, total |
| cost | xarajat, tannarx | себестоимость, затраты | cost, cogs |
| quantity | miqdor, soni, dona | количество, шт | quantity, qty, units |
| customer_id | mijoz, mijoz_id, xaridor | клиент, клиент_id, покупатель | customer_id, client_id |
| order_id | buyurtma, buyurtma_id, chek, bitim_id | заказ, номер_заказа, сделка | order_id, sale_id, deal_id |
| status | holat, status, to'lov_holati | статус, состояние | status, state |
| stage | bosqich, etap | этап, стадия | stage |
| owner | mas'ul, menejer, sotuvchi | ответственный, менеджер | owner, manager, rep |
| source | manba, kanal_manba | источник | source |
| target | reja, maqsad, kvota | план, цель, квота | target, quota, plan |
Stage-order dictionary (used by funnel): `lead < qualified < proposal < negotiation < won/lost` plus Uzbek and Russian equivalents (`so'rov`, `saralangan`, `taklif`, `muzokara`, `yutildi/yutqazildi`; `лид`, `квалификация`, `предложение`, `переговоры`, `выиграно/проиграно`). If an order cannot be inferred, estimate it by typical flow (counts decreasing) and show «taxminiy tartib».

---

## Appendix D — Final Report the agent must print at the end

1. File tree. 2. Test summaries per suite (counts, duration). 3. Planted-pattern tables for the 4 core datasets (detected yes/no, negative control). 4. Uzbek/English golden results (router coverage and accuracy; live-model accuracy if a key was available, otherwise "not run"). 5. Security check results (audits, S-row test mapping). 6. Accessibility, contrast and performance numbers; measured memory. 7. List of unreviewed Uzbek strings count. 8. Assumptions, deviations and known limitations. 9. Exact commands: run tests, run locally (both ways), `docker compose up`, deploy to Render, create the first admin, generate keys.
