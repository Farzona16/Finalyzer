# FINALYZER — Build Specification for an AI Coding Agent

**Version:** 2.0 (English-only MVP) · **Owner:** Farzonaxon · **Role of the author:** Product Manager / Solution Architect
**Deliverable:** a complete, tested, runnable repository that (1) runs on a local laptop, (2) deploys to Render via Docker.

---

## 0. READ THIS FIRST — Agent Operating Protocol

You are the sole engineer on this project. This document is complete. Follow it literally.

**R1. Do not ask questions.** Every decision you need is in Section 2 (Decision Log). If something is genuinely unspecified, choose the simplest option that respects Section 1 principles, write one line to `ASSUMPTIONS.md` (what, why), and continue. The only acceptable reason to stop is a missing secret (for example an LLM API key); in that case continue in offline mode (Section 6, P4) and say so in the final report.

**R2. Work phase by phase (P0 → P9).** A phase is finished only when its acceptance tests pass. Do not start the next phase before that. If a test fails, fix the code, not the test (unless the test itself is provably wrong; then record it in `ASSUMPTIONS.md`).

**R3. Output complete files.** No placeholders, no `TODO`, no `...`, no "rest of file unchanged". Every file you mention must exist and be runnable.

**R4. Two working modes.**
- *Filesystem mode* (Claude Code, Cursor, Codex CLI, etc.): create files, run commands and tests yourself, and continue through all phases without stopping.
- *Chat mode* (no filesystem): output one phase per reply, with complete file contents and the exact commands to run. End each reply with `PHASE n COMPLETE. Reply "next".` and nothing else.

**R5. Status report after every phase** (3–8 lines): files created or changed, tests run and result, deviations from this spec.

**R6. Never invent library APIs.** If unsure how a library works, check the installed version (`pip show`, `help()`, read its source) and write a small test. Pin versions after installing (P0).

**R7. Code style (mandatory).** Simple, readable Python 3.12 with type hints and short docstrings. Use common functions. No metaprogramming, no clever one-liners, no unnecessary abstractions. Functions under about 40 lines. Comments explain *why*, not *what*. A junior developer must be able to read every file.

**R8. Truthfulness.** Never claim a feature works unless a test proves it. `KNOWN_LIMITATIONS.md` must be honest.

---

## 1. Product Definition

**One sentence:** Finalyzer lets a bank analyst or a business owner upload their own tabular data and, within minutes, get a clean interactive dashboard with slicers, grounded insights, concrete recommendations, and a natural-language Q&A box, while keeping the data private.

**Users:** (a) bank staff (risk, marketing, branch managers); (b) small and medium business owners with no BI skills.

**Core principles (in priority order):**
1. **Numbers come from code, never from the LLM.** All calculations run in DuckDB or pandas/numpy. The LLM writes SQL or wording only.
2. **One definition per metric.** A Metrics Registry is the single source of truth (this replaces Power BI DAX measures).
3. **Detect first, narrate second.** Insights are found by deterministic detectors. The LLM only turns verified findings into readable text, and every number in that text is verified against the findings.
4. **Privacy by construction.** Every external call passes through one Egress Gate that enforces the privacy mode and writes an audit record.
5. **Fail visibly, never silently.** Wrong or uncertain results are flagged or refused, not hidden.
6. **The product works without an LLM.** Dashboard, slicers, detectors and template-based insights must work with no API key.

**Non-goals for this version:** Uzbek or other languages (English only; leave i18n hooks), multi-user accounts and roles, persistent storage, live database connections, forecasting, loan-portfolio demo, PDF export, Presidio/spaCy, fine-tuned models.

---

## 2. Decision Log (binding — do not revisit)

| # | Decision | Reason |
|---|---|---|
| D1 | Python 3.12, Streamlit UI, DuckDB engine, Plotly charts, pandas + numpy, sqlglot, Pydantic v2 + pydantic-settings, PyYAML, openpyxl, LiteLLM, pytest, ruff | Small, common, well-known stack |
| D2 | **Single flat view.** User chooses one fact table; dimension tables are LEFT JOINed into one view named `base`. All analytics and all LLM SQL run against a view named `data` (= `base` + active slicer filters). The LLM never sees raw tables. | Removes most SQL errors; makes slicers trivial |
| D3 | Column **roles** (`date`, `amount`, `cost`, `quantity`, `customer_id`, `order_id`, `status`) are auto-detected by name synonyms and type, shown in the UI, and editable. Metrics are written with role placeholders. | Works on arbitrary uploaded files |
| D4 | PII columns (name, phone, email, card number, national ID/passport patterns) are **dropped from `base`** and listed in the report. No user override in v1. | Simple and safe |
| D5 | No Presidio. PII detection = column-name hints + regex + Luhn check on a value sample. | Heavy dependencies break small deployments |
| D6 | Privacy modes: `demo` (cloud LLM, synthetic data only), `cloud` (cloud LLM, real data, schema + aggregates only), `local` (Ollama only, no external egress). Default `demo`. | Matches the product story |
| D7 | Category values (distinct values of low-cardinality text columns, up to 30) are sent to the LLM in `demo` and `local` modes. In `cloud` mode they are sent only if `SEND_CATEGORY_VALUES=true` (default `false`). | LLM needs values like `'completed'` to filter correctly; this is an explicit privacy trade-off, documented in the UI |
| D8 | In `cloud` mode the LLM never receives query result rows. Q&A answers are described by a deterministic template. In `demo` and `local` modes at most 50 result rows may be sent. | Prevents leakage through results |
| D9 | Insights: deterministic detectors → ranked findings → narrator. Narrator has 3 implementations behind one interface: `LiveNarrator` (LLM), `TemplateNarrator` (no LLM), `ReplayNarrator` (not needed; use Template). If the live narrator fails grounding twice, fall back to the template narrator. | Always produces output |
| D10 | Q&A: LLM returns JSON `{intent, sql, clarification, chart_hint, metric_ids}`. SQL candidates: 3 (config). All are executed; majority result wins; disagreement marks the answer **Unverified**. | Catches silent errors |
| D11 | Offline/replay mode: when no API key or `LLM_MODE=offline`, Q&A answers only the golden demo questions using stored expected SQL; other questions get a clear "offline mode" message. | Demo works without internet |
| D12 | Slicer values are validated against known columns and values, and SQL literals are escaped. | Prevents injection through the UI |
| D13 | Model names are never hard-coded. `LLM_MODEL` is read from environment. `.env.example` shows placeholders. | Model names go stale |
| D14 | Authentication: optional single shared password (`APP_PASSWORD`), compared with `hmac.compare_digest`. | Enough for demo |
| D15 | Sessions live in memory only. Uploaded data and audit log are deleted when the session ends or the user clicks "Clear session". | Privacy |

---

## 3. Architecture

```
Browser ─► Streamlit app (app/main.py)
              │  st.session_state → SessionContext
              ▼
   ┌───────────────────── core (pure Python, no Streamlit imports) ─────────────────────┐
   │ ingest/     load → clean → profile → pii → roles → relationships → flat view       │
   │ metrics/    registry (YAML packs) → SQL builders → time intelligence               │
   │ query/      sql_validator → executor → nl2sql agent (candidates, voting) → charts  │
   │ dashboard/  filters (slicers) → kpi → chart specs                                  │
   │ insights/   detectors → ranking → narrator (live | template) → grounding → cards   │
   │ llm/        gateway (LiteLLM) → egress gate → audit log → providers (live|offline) │
   └────────────────────────────────────────────────────────────────────────────────────┘
                     DuckDB in-memory (external access disabled, config locked)
```

**Data flow rule:** UI → core → DuckDB. Only `llm/gateway.py` may talk to an LLM, and only through `EgressGate.send()`.

### 3.1 Repository layout (create exactly this)

```
finalyzer/
├── README.md
├── ASSUMPTIONS.md
├── KNOWN_LIMITATIONS.md
├── DEPLOY.md
├── pyproject.toml              # ruff + pytest config
├── requirements.txt            # compatible ranges
├── requirements.lock.txt       # exact pins from pip freeze (created in P0, refreshed in P9)
├── .env.example
├── .gitignore                  # includes .env, .venv, __pycache__, data/tmp
├── .dockerignore
├── Dockerfile
├── render.yaml
├── .streamlit/config.toml
├── app/
│   ├── main.py                 # Streamlit entry
│   ├── state.py                # SessionContext
│   ├── ui_data.py  ui_dashboard.py  ui_insights.py  ui_ask.py  ui_audit.py
│   ├── theme.py                # colors, plotly template, CSS
│   └── i18n/en.py              # all UI strings (English only now)
├── core/
│   ├── config.py  errors.py  logging_setup.py  models.py
│   ├── ingest/  {loader.py, cleaner.py, profiler.py, pii.py, roles.py, relationships.py, flatten.py}
│   ├── metrics/ {registry.py, builder.py, timeintel.py, packs/{generic,retail,bank}.yaml}
│   ├── query/   {sql_validator.py, executor.py, nl2sql.py, charts.py, prompts.py}
│   ├── dashboard/ {filters.py, kpi.py, chartspecs.py}
│   ├── insights/ {detectors.py, ranking.py, narrator.py, grounding.py, actions.py, nextsteps.py}
│   └── llm/     {gateway.py, egress.py, audit.py, providers.py}
├── scripts/
│   ├── generate_demo_data.py   # deterministic synthetic data with planted patterns
│   ├── eval_golden.py          # accuracy of a live model on the golden set
│   ├── perf_check.py           # 1M-row timing
│   └── check_payloads.py       # scans audit payloads for PII patterns
├── data/demo/                  # generated CSVs + golden_questions.yaml
└── tests/                      # see Section 11
```

---

## 4. Configuration (`core/config.py`, `.env.example`)

Use `pydantic-settings`, reading environment and `.env`. Provide `.env.example` with all keys and comments.

| Variable | Default | Meaning |
|---|---|---|
| `PRIVACY_MODE` | `demo` | `demo` / `cloud` / `local` |
| `LLM_MODE` | `live` | `live` / `offline` (auto-switches to offline if no key and mode is not `local`) |
| `LLM_MODEL` | *(empty)* | LiteLLM model string, e.g. `provider/model-name` (placeholder in example; user fills in) |
| `LLM_API_KEY` | *(empty)* | Secret |
| `LLM_API_BASE` | *(empty)* | For Ollama, e.g. `http://localhost:11434` |
| `LLM_TIMEOUT_S` | `60` | Per call |
| `LLM_MAX_RETRIES` | `3` | Exponential backoff with jitter |
| `SQL_CANDIDATES` | `3` | Candidates per question |
| `SESSION_TOKEN_BUDGET` | `200000` | Hard stop for LLM calls in a session |
| `SEND_CATEGORY_VALUES` | `false` | Only relevant in `cloud` mode |
| `MAX_FILE_MB` | `50` | Upload limit |
| `MAX_ROWS` | `1000000` | Row limit after load |
| `QUERY_TIMEOUT_S` | `10` | DuckDB statement timeout |
| `RESULT_ROW_LIMIT` | `10000` | Wrapper LIMIT |
| `DUCKDB_MEMORY_LIMIT` | `1GB` | DuckDB setting |
| `K_ANON_MIN` | `5` | Minimum group support in findings sent to cloud |
| `APP_PASSWORD` | *(empty)* | Empty = no gate |
| `LOG_LEVEL` | `INFO` | |

`Settings` must validate: `PRIVACY_MODE=local` requires `LLM_API_BASE`; otherwise refuse to start with a clear message.

---

## 5. Core Contracts (`core/models.py`)

Implement these as Pydantic v2 models or dataclasses exactly (extra fields allowed, names fixed):

- `ColumnInfo`: `name, dtype ("integer"|"float"|"text"|"date"|"boolean"), null_pct, distinct, is_pii, pii_reason, role (str|None), categories (list[str]|None)`.
- `CleaningAction`: `column, action, count, detail`.
- `Relationship`: `fact_col, dim_table, dim_col, containment, cardinality ("many_to_one"|"one_to_one")`.
- `DatasetModel`: `dataset_id, fact_table, tables (dict name → row_count), relationships, columns (list[ColumnInfo], flat view), roles (dict role → column), row_count, dropped_pii (list[str]), warnings (list[str]), cleaning_report (list[CleaningAction]), version (hash string)`.
- `Metric`: `id, label, expr, requires (list[str]), requires_values (dict), unit ("currency"|"count"|"percent"|"ratio"), good_direction ("up"|"down"|"neutral"), additive (bool), description`.
- `FilterSpec`: `date_range (tuple[date,date]|None), categorical (dict[str, list[str]]), numeric (dict[str, tuple[float,float]])`.
- `QueryResult`: `sql, columns, rows, row_count, runtime_ms, verified (bool), candidates_total, candidates_agreeing, metric_ids, warnings`.
- `Finding`: `id, type, metric_id, title (short machine-readable label), facts (dict[str, float|str]), segment (dict|None), period_label, support (int), severity (0..1), confidence (0..1)`.
- `RecommendationCard`: `finding_id, observation, evidence (list[str]), likely_cause, actions (list[str], 1–2), expected_impact (str|None), priority ("High"|"Medium"|"Low"), confidence ("High"|"Medium"|"Low"), track_metric`.
- `AuditEvent`: `ts, purpose, mode, provider_model, payload_text (exact string sent), payload_bytes, blocked (bool), block_reason, tokens_in, tokens_out, latency_ms`.
- `AppError` hierarchy in `core/errors.py`: `UserFacingError(message)`, `ValidationError`, `PrivacyBlockedError`, `LLMUnavailableError`, `QueryTimeoutError`. UI shows `UserFacingError.message`; unknown exceptions show "Something went wrong (ref: <8-char id>)" and log the traceback with that id.

---

## 6. Phases

### P0 — Bootstrap
**Build:** repo layout, `pyproject.toml` (ruff line length 100, pytest testpaths), `requirements.txt`, `.gitignore`, `.env.example`, `core/config.py`, `core/errors.py`, `core/logging_setup.py` (JSON-style single-line logs, never log data values), empty `__init__.py` files, `ASSUMPTIONS.md`. Create venv, install, run `pip freeze > requirements.lock.txt`.
**Accept:** `ruff check .` clean; `pytest -q` runs (may have zero tests); settings test: `PRIVACY_MODE=local` without `LLM_API_BASE` raises a clear error.

### P1 — Demo data and golden set
**Build:** `scripts/generate_demo_data.py` (seed 42, deterministic; regenerates identical files) producing `data/demo/retail/*.csv` and `data/demo/bank/*.csv` per Appendix A, with the 8 planted patterns per dataset. Also write `data/demo/golden_questions.yaml`: 40 entries (20 retail, 20 bank) `{id, dataset, question, expected_sql}` where `expected_sql` queries the flat view `data` (column names as they will appear after flattening, Appendix A). Question mix: simple aggregate, group-by, top-N, time series, filters, ratios, month-over-month, year-over-year, distinct counts, HAVING, date parts. Keep all 40 questions **out of any prompt or few-shot example**.
**Accept:** running the script twice gives byte-identical files; total demo data under 25 MB; `tests/test_golden_sql.py` builds each dataset's flat view with pandas→DuckDB (temporary helper, replaced by real ingestion in P2) and asserts all 40 `expected_sql` run and return at least one row.

### P2 — Ingestion, cleaning, profiling, PII, roles, relationships, flat view
**Build (`core/ingest/`):**
- `loader.py`: accept CSV, XLSX (each sheet = table). CSV: try encodings `utf-8`, `utf-8-sig`, `cp1251`, `latin-1`; sniff delimiter among `, ; \t |`. Enforce `MAX_FILE_MB`, `MAX_ROWS`. Never execute formulas (read cell values as data). Table names sanitized to `snake_case`.
- `cleaner.py`: strip and snake_case column names (dedupe with suffixes); trim strings; convert empty-like strings (`""`, `NA`, `N/A`, `null`, `-`) to null; parse numeric text (thousand separators, currency symbols, comma decimals) when ≥ 95% of non-null values parse; parse dates (try ISO, `dd.mm.yyyy`, `mm/dd/yyyy`, `dd/mm/yyyy`; if ambiguous choose the format that parses all values, else `dayfirst=True` and add a warning); drop fully empty rows and columns; report exact duplicate rows but **do not delete** them (UI checkbox default off). Every change becomes a `CleaningAction`. Never modify the original DataFrame in place.
- `profiler.py`: per column dtype, null_pct, distinct, `categories` when text and distinct ≤ 30.
- `pii.py`: flag a column as PII when its name matches hints (`name, fullname, first_name, last_name, phone, mobile, email, card, pan, passport, ssn, national_id, inn, pinfl`) **or** ≥ 30% of a 1,000-value sample matches: email regex, phone regex (7–15 digits with optional `+ ( ) -`), 13–19 digit card number passing Luhn. Exclude columns named like `*_id` from the value-pattern rule unless the name hints match. Record `pii_reason`.
- `roles.py`: synonym table in Appendix B; a role is assigned only when the type fits (`amount/cost/quantity` numeric, `date` date). If multiple candidates, pick the first by synonym priority. Return roles dict; missing roles are simply absent.
- `relationships.py`: given a chosen fact table, for each other table find columns whose values are unique (dimension key candidates); for each fact column of compatible type compute containment `|A ∩ B| / |A|` (non-null) in DuckDB; propose when containment ≥ 0.95, tie-break by name similarity (`difflib.SequenceMatcher` ≥ 0.6 boosts). Return proposals; UI confirms.
- `flatten.py`: create `base` view: `SELECT f.*, d.<cols> FROM fact f LEFT JOIN dim d ON ...`, excluding PII columns and dimension key duplicates; on a name collision prefix the dimension column with `<table>_`. Then create `data` as `SELECT * FROM base` (rebuilt when filters change). Connection setup: `SET memory_limit`, `SET enable_external_access=false`, then `SET lock_configuration=true` after registering DataFrames.
- Fact table auto-pick: table that has a date-typed column and the most rows.
**Accept (tests):** messy fixtures (`tests/fixtures/messy_*.csv`: mixed date formats, `1 234,50` numbers, `N/A` tokens, duplicate rows, cp1251 encoding, `;` delimiter) load with the expected dtypes; PII columns are detected in both demo datasets (`name, phone, email` in retail customers; `full_name, phone` in bank customers) and absent from `base`; roles for both demo datasets match Appendix A exactly; relationships proposed for both demos equal the expected set with containment ≥ 0.95; a corrupt file raises `UserFacingError`, not a traceback.

### P3 — Metrics registry and time intelligence
**Build (`core/metrics/`):**
- Pack format (YAML), one file per domain (Appendix C gives the exact packs `generic`, `retail`, `bank`):
```yaml
pack: retail
primary_metric: revenue
metrics:
  - id: revenue
    label: Revenue
    expr: "SUM({amount})"
    requires: [amount]
    unit: currency
    good_direction: up
    additive: true
    description: "Total sales amount."
```
- `registry.py`: load packs; `available_metrics(dataset_model)` returns metrics whose required roles are mapped and whose `requires_values` exist in the data (for example `status` contains `completed`). Auto-select the pack: `retail` if roles include `order_id`+`amount`+`quantity`; `bank` if a `status` column contains `completed`/`failed` values and `amount` exists; else `generic`. The user can override in the UI. Users can also edit a metric's expression in a text box; edits are validated by executing it once on `data` (must return one numeric value) before acceptance.
- `builder.py`: `metric_sql(metric, roles) -> str` replaces `{role}` placeholders with quoted column names; refuses unknown placeholders.
- `timeintel.py`: SQL builders returning DuckDB SQL strings for: `series(metric, grain)` (day/week/month/quarter via `date_trunc`), `period_compare(metric, start, end)` (current vs the immediately preceding period of equal length), `yoy(metric, grain)`, `ytd(metric)`, `share_of_total(metric, dim)`, `top_n(metric, dim, n)`. Grain auto-choice: span ≤ 62 days → day; ≤ 180 → week; else month.
**Accept:** metric parity test: for every available metric on both demo datasets, the value from DuckDB equals an independent pandas computation within `1e-6` relative error; every time-intelligence builder output executes without error on both datasets; a metric referencing an unmapped role is reported as unavailable, never as an error.

### P4 — Query engine, LLM gateway, Egress Gate, audit
**Build:**
- `query/sql_validator.py` using `sqlglot` (`read="duckdb"`): exactly one statement; root must be `SELECT` (CTEs and `UNION` allowed); the only physical table allowed is `data` (CTE names allowed); no catalog/schema qualifiers; blocked function names include `read_csv, read_csv_auto, read_parquet, read_json, read_json_auto, read_text, read_blob, glob, sniff_csv, query, query_table, getenv, current_setting, pragma_*, duckdb_*, httpfs, load_extension`; blocked statement kinds: everything except SELECT. Return the normalized SQL or raise `ValidationError(reason)`. After validation run `EXPLAIN <sql>` to catch unknown columns before execution.
- `query/executor.py`: wraps SQL as `SELECT * FROM (<sql>) LIMIT RESULT_ROW_LIMIT`; runs in a worker thread with `QUERY_TIMEOUT_S`, calling `con.interrupt()` on timeout → `QueryTimeoutError`. Returns `QueryResult`. Result hash for voting: round floats to 4 decimals, stringify, sort rows, SHA-256.
- `llm/providers.py`: `LiveProvider` (LiteLLM `completion`, temperature and timeout from config, retries with exponential backoff and jitter on rate-limit/5xx/timeouts, JSON-only responses parsed and validated with Pydantic; one automatic repair retry on invalid JSON) and `OfflineProvider` (golden-question lookup; normalized match: lowercase, strip punctuation and extra spaces).
- `llm/egress.py`: `EgressGate.send(purpose, payload_builder_output)` steps in order: (1) mode check: `local` allows only providers whose `api_base` is loopback/private, otherwise `PrivacyBlockedError`; (2) payload scan: reject if it contains an email, a Luhn-valid 13–19 digit number, or a phone-like string outside allowed fields; (3) size and token-budget check; (4) write `AuditEvent` (including exact payload text) **before** sending; (5) send; (6) update tokens and latency. Blocks are also audited.
- `llm/audit.py`: in-memory list per session, `total_bytes_sent`, export to JSONL (download button), cleared on session end.
- `query/nl2sql.py`: builds the prompt (Section 7.1), asks for `SQL_CANDIDATES` candidates (first at temperature 0, others at 0.5), validates and executes each (max 2 repair retries per candidate, feeding the exact error back), groups by result hash, picks the largest group. `verified = (largest_group_size >= 2 and largest_group_size > total/2)` when candidates ≥ 3, or `True` when only 1 candidate is configured and it executed. If `intent` is `clarify` or `refuse`, return that without executing.
- `query/charts.py`: deterministic chart choice from result shape: 1×1 → KPI card; date + numeric → line; text + numeric → bar (horizontal if > 8 categories, top 15); text + 2–3 numerics → grouped bar; otherwise table. LLM `chart_hint` is advisory only.
**Accept (tests):**
- `tests/test_sql_validator.py`: a corpus of **≥ 40 malicious or invalid statements** in `tests/data/malicious_sql.txt` (DDL, DML, multi-statement, comment tricks, file/URL functions, `PRAGMA`, `ATTACH`, `COPY`, `INSTALL`, recursive CTE bombs treated by timeout, reference to other tables, quoted-identifier tricks, `UNION` to system tables) — 100% rejected; ≥ 25 valid statements — 100% accepted.
- Timeout test: a deliberately heavy cross join is stopped within `QUERY_TIMEOUT_S + 2` seconds and the connection stays usable.
- Egress tests: `local` mode blocks a public API base; a payload containing a valid card number or an email is blocked and audited; audited payload text equals what the fake provider received (byte-for-byte).
- Voting test using a fake provider: 3 agreeing candidates → verified; 2 agree + 1 differs → verified with note; all differ → `verified=False`.
- Offline test: every golden question returns its expected SQL result; an unknown question returns the offline message.

### P5 — Dashboard and slicers
**Build (`core/dashboard/`, `app/ui_dashboard.py`, `app/theme.py`):**
- `filters.py`: `FilterSpec` → SQL `WHERE` text with **escaped literals** (single quotes doubled, numbers via `float()`, dates via `date.fromisoformat`), columns validated against the known flat columns. `apply_filters(con, spec)` recreates the `data` view. `where_sql(spec, date_override=None)` is used for previous-period KPI queries against `base`.
- Slicers (Streamlit sidebar): date range (default: full span); up to 4 categorical multi-selects for low-cardinality text columns (distinct ≤ 50; choose the four with the most useful cardinality 3–30); numeric range for the `amount` role; "Clear filters" button; an always-visible line listing active filters and the resulting row count.
- `kpi.py`: for each available metric (max 6 shown, primary first): value over the selected range, value over the previous equal-length range, delta % (for `percent` unit show percentage-point difference), color by `good_direction`. If previous range has no data show "n/a".
- `chartspecs.py` + UI: (1) trend of the primary metric at auto grain with a toggle to overlay the previous year when ≥ 13 months exist; (2) breakdown bar of the primary metric by a user-selected dimension (default: highest-variance dimension); (3) Top-N table (N=10) with share %; (4) second metric trend selectable from a dropdown; (5) data table preview (first 200 rows of `data`). All charts are built from templates, never from LLM code.
- Design: one palette (defined in `theme.py`), consistent Plotly template, KPI cards as HTML via `st.markdown(unsafe_allow_html=True)` using only static, escaped strings, clear titles with units, empty-state messages, light/dark friendly colors, responsive columns. Use `st.cache_data` keyed by `(dataset.version, filter_hash, query_key)` (pass the DuckDB connection with a leading underscore so it is not hashed).
**Accept:** for 10 random filter combinations per demo dataset, every KPI on screen equals a pandas recomputation on the filtered DataFrame (test at the core layer, not by clicking); slicer application plus all dashboard queries on 1M rows finish in ≤ 2 s on a typical laptop (`scripts/perf_check.py` prints timings; report actual numbers in `KNOWN_LIMITATIONS.md`); an empty filter result shows a friendly message, not an exception; injection test: a categorical value like `x' OR '1'='1` returns zero rows and does not alter the query.

### P6 — Insight engine, recommendations, next steps
**Build (`core/insights/`):** all detectors operate on `data` (respecting slicers) using the primary and other available metrics. Require ≥ 3 periods for time detectors, otherwise skip and add a note. Definitions:

| Detector (`type`) | Rule |
|---|---|
| `trend` | Monthly series. Last complete month vs previous month (also vs same month last year if available). Flag when `abs(pct_change) >= max(0.10, 1.5 × std(historical month-over-month % changes))`. For `percent` unit metrics use absolute difference in points and flag at ≥ 2 pp. Severity = `min(1, abs(pct_change)/0.5)`. Last month is "complete" if the data's max date reaches the month's last day; otherwise use the previous month and say so. |
| `anomaly` | Daily series (only if span ≥ 60 days). Remove weekday effect by subtracting each weekday's median. Robust z = `0.6745 × (x − median) / MAD`. Flag `abs(z) >= 3.5` within the last 90 days; report at most 3, largest first. Severity = `min(1, abs(z)/10)`. |
| `concentration` | For each dimension with ≥ 5 categories and additive primary metric: share of top-1 and top-3. Flag when top-1 ≥ 30% or top-3 ≥ 60%. Severity = top-1 share. Include HHI in facts. |
| `driver` | For additive metrics with a flagged `trend`: per dimension, per category, delta between last and previous month; list the top 2 categories by absolute contribution and their share of total delta; flag when the top category explains ≥ 40% of the total change. Severity = trend severity × explained share. |
| `segment_gap` | For ratio/average metrics (for example average order value, failed rate): per dimension compare best vs worst category among categories with support ≥ 100 rows; flag when ratio ≥ 2 (or difference ≥ 5 pp for percent metrics). Severity = `min(1, log2(ratio)/3)`. |
| `segment_momentum` | For additive primary metric and each dimension: growth of the last 3 months vs same 3 months a year earlier per category (needs ≥ 15 months of data); flag categories whose growth differs from overall by ≥ 25 pp with support ≥ 100 rows. |
| `churn` | Needs `customer_id` and `date`. Churn rate for month m = customers active in m−1 and not active in m, divided by customers active in m−1. Compare last complete month with the mean of the previous 6; flag if ≥ +10 pp (worse) or ≤ −10 pp (better). Also report the new-customer share. |
| `data_quality` | Any column (non-PII, kept) with null_pct ≥ 20%, and date gaps > 7 days inside the span. Severity = null_pct. |

- **Suppression:** in `cloud` mode drop any finding whose `support < K_ANON_MIN` before it can reach the LLM (also keep it out of the LLM payload for any group smaller than that).
- `ranking.py`: `score = severity × confidence`, where `confidence = min(1, n_periods/12) × min(1, support/100)` (use 1 for support where not applicable). Show the top 5; put the rest under "More findings".
- `actions.py`: a static library mapping finding type (and direction) to 1–2 generic action templates, used by the template narrator and as guidance for the live narrator. Example: concentration → "Reduce dependence on {segment} by testing offers in the next-largest segments"; driver (negative) → "Investigate {segment} first: it explains {share}% of the change".
- `narrator.py`: interface `narrate(findings, facts) -> list[RecommendationCard]`. `TemplateNarrator` fills templates from finding facts (no LLM). `LiveNarrator` sends only the findings JSON (Section 7.2), receives cards JSON, validates with Pydantic, runs grounding (below); on failure retries up to 2 times, then falls back to the template narrator and marks the section "generated without AI".
- `grounding.py`: extract every number from every text field of every card (handle `1,234.5`, `12%`, `3.2x`, `$5k`, `2 million`). A number is *grounded* if it matches (within rounding to the displayed precision, or 0.5% relative) a value in the allowlist built from the finding facts plus derived values the code computed (percent changes, shares, differences). Ignore integers 0–10, years 1900–2100, and digits inside category or metric names. Any ungrounded number fails the card.
- `nextsteps.py`: deterministic "Next steps" block: (a) missing roles that limited analysis (for example "No cost column: gross profit and margin could not be computed. Add one to unlock them."); (b) up to 3 metrics to track monthly (primary metric, the metric with the largest flagged trend, and the churn or failed-rate metric when available); (c) data-quality fixes.
- Wording rules for the narrator and templates: describe drivers as **hypotheses** ("likely", "may"), never as proven causes; never promise business results; no advice on individual customers' credit or eligibility.
**Accept:** on each demo dataset the detectors reproduce the planted patterns of Appendix A: **≥ 7 of 8** matched by (type, metric or dimension, segment) — assert each individually and print the table; false-positive guard: on a synthetic "boring" dataset (stable series with noise, no patterns) at most 1 finding is produced; grounding test: a card containing an invented number fails, a correct one passes, formatting variants (`12%`, `12.0 %`, `twelve percent` not required) behave as documented; template narrator produces one card per top finding with zero LLM calls; `cloud`-mode payload test: no finding with support < 5 is present in any audited payload.

### P7 — Application assembly
**Build (`app/`):** tabs **Data · Dashboard · Insights · Ask · Audit**; sidebar holds the privacy-mode badge, dataset selector (Upload / Demo: Retail / Demo: Bank), slicers, "Clear session".
- **Data tab:** upload (CSV/XLSX, multiple files); table list with row counts; cleaning report; PII report ("Removed from analysis: …"); fact-table selector; relationship checkboxes (proposals pre-ticked when containment ≥ 0.95); role mapping selectors (editable); metric pack selector and metric list with editable expressions; a banner in `demo`/`cloud` modes: "Do not upload real personal data to the public demo." A "Confirm and build" button creates `base`/`data`, sets `dataset.version`.
- **Dashboard tab:** as P5. **Insights tab:** top findings as cards (headline, evidence, likely cause, actions, priority, confidence, "how we know" expander listing the detector and its numbers), "More findings", "Next steps"; badge "AI-written" or "Template-written". **Ask tab:** input box, 6 suggested questions (from the golden set for demo datasets), answer area showing chart/table, the answer text, a `Verified` or `Unverified – candidates disagreed` badge, an expander "How this was computed" (SQL, metrics used, active filters, rows, runtime), clarification prompts rendered as buttons when `intent=clarify`. **Audit tab:** table of `AuditEvent`s, exact payload viewer, big counters "Bytes sent outside: N" and "Blocked calls: N", JSONL download.
- Global: password gate (D14); error boundary wrapping each tab (correlation id); LLM failures never break the Dashboard; all user-visible strings come from `app/i18n/en.py`; session inactivity cleanup is best effort (document the limit).
**Accept:** `streamlit run app/main.py` starts with no API key; the Demo Retail path works end to end offline (load → build → dashboard → insights (template) → Ask a suggested question → audit shows 0 bytes sent); `tests/test_app_smoke.py` uses `streamlit.testing.v1.AppTest` to run the demo path and asserts no exception elements appear.

### P8 — Evaluation and hardening
**Build:** `scripts/eval_golden.py --model <litellm string> [--candidates N]` runs all 40 golden questions through the real NL→SQL pipeline and prints a table: per-question pass/fail (result-set equality, order-insensitive, floats rounded to 2 decimals), overall accuracy, verified-vs-unverified breakdown, mean latency, token totals; writes `reports/eval_<model>_<date>.json`. `scripts/check_payloads.py` scans an exported audit JSONL for email, phone and card patterns and exits non-zero on any hit. `scripts/perf_check.py` builds a 1M-row dataset and prints timings for load, view build, 5 dashboard queries, detectors. Add chaos tests with a fake provider: timeout, 429, 500, malformed JSON, empty output — the app must always end in a user-readable state.
**Accept:** all tests green; `ruff check .` clean; the eval script runs offline against the OfflineProvider and reports 40/40 (proves harness correctness). With a real key, run once and store the report; do not claim a percentage that is not in a stored report.

### P9 — Packaging and deployment
**Build:**
- `README.md`: prerequisites; Windows/macOS/Linux setup commands (venv, install, `.env`), run tests, generate demo data, start the app, run Docker locally, run eval; screenshots not required.
- `Dockerfile` (python 3.12-slim): non-root user; install from `requirements.lock.txt`; copy code; run `python scripts/generate_demo_data.py` at build time; expose port; `CMD` runs `streamlit run app/main.py --server.port ${PORT:-8501} --server.address 0.0.0.0 --server.headless true`. Keep the image small; no build tools left behind if avoidable.
- `.streamlit/config.toml`: theme colors from `theme.py`, `server.maxUploadSize = 50`, `browser.gatherUsageStats = false`. Keep XSRF protection enabled; disable only if the deployed app cannot connect, and then record it in `KNOWN_LIMITATIONS.md`.
- `render.yaml`: one Docker web service, health check path `/_stcore/health`, environment variables declared (secrets with `sync: false`), `PRIVACY_MODE=demo`, `LLM_MODE=live`, plan value left as a documented setting.
- `DEPLOY.md`: step-by-step Render deployment (push to GitHub, create service from `render.yaml`, set secrets, verify), a smoke-test checklist, rollback (redeploy previous commit), how to switch to offline mode if the LLM key fails, and a memory check: run the container locally, record idle and peak memory during the demo path, write the numbers into the document, and warn if the chosen Render plan has less RAM than peak × 1.3.
- `KNOWN_LIMITATIONS.md`: honest list (English only, single fact table, no persistence, LLM SQL accuracy is measured not guaranteed, category values privacy trade-off, no formal security certification, free hosting tiers may sleep, etc.), plus the real numbers from `perf_check` and any eval report.
**Accept:** `docker build` and `docker run -p 8501:8501` work on the laptop and pass the P7 smoke path; `render.yaml` validates as YAML and matches Dockerfile port handling; `DEPLOY.md` contains actual measured memory numbers.

---

## 7. Prompts (implement in `core/query/prompts.py` and `core/insights/narrator.py`)

### 7.1 NL→SQL prompt
**System:** "You translate business questions into one DuckDB SQL query. Rules: query only the table `data`. Use only the listed columns. Use the listed metric expressions when the question asks for a defined metric. Output a single SELECT (CTEs allowed). Always alias computed columns. Use DuckDB syntax (`date_trunc`, `strftime`, `FILTER (WHERE ...)`). Never invent columns or values. If the question is ambiguous, return intent `clarify` with one short question. If it asks for anything other than reading data, return intent `refuse`. Return JSON only: `{"intent": "query|clarify|refuse", "sql": string|null, "clarification": string|null, "chart_hint": "line|bar|table|kpi|null", "metric_ids": [string]}`."
**User payload (built by code):** table description of `data` (column, type, role, null %, min/max for numeric and date), category values per D7, available metrics (id, label, expression), the current date range, the question. Include 6 fixed few-shot examples that are **not** part of the golden set.
**Repair turn:** append the exact DuckDB or validator error text and ask for corrected JSON.

### 7.2 Narrator prompt
**System:** "You write business insight cards from verified findings. Use only numbers present in `facts`. Do not compute new numbers. Describe causes as hypotheses. Do not promise outcomes. Each card has: observation, evidence (list of short strings quoting given facts), likely_cause, actions (1–2 concrete steps), expected_impact (only if a number is provided for it, otherwise null), priority, confidence, track_metric. Return JSON: `{"cards": [ ... ]}` matching the schema. Write in English, plain and specific, no filler."
**User payload:** the top findings (id, type, metric label, direction, good/bad, facts, segment, period label) and the action templates as hints.

---

## 8. Security Requirements (all testable)

1. Only `EgressGate` reaches an LLM; a test greps the codebase to ensure `litellm` is imported in exactly one module.
2. Payload text in `AuditEvent` is byte-identical to what was sent.
3. `cloud`/`demo` payloads never contain raw rows, PII values, or (in `cloud` with default settings) category values.
4. SQL guardrails as in P4; DuckDB external access disabled and configuration locked.
5. Secrets only from environment; `.env` git-ignored; logs never contain keys, payloads or data values.
6. Uploaded files and audit logs are cleared on "Clear session".
7. UI HTML uses only escaped strings.
8. `pip-audit` is run in P9 if available; report results in `KNOWN_LIMITATIONS.md`.
9. No claim of compliance with any law or standard anywhere in the product text.

---

## 9. Error Handling Matrix

| Situation | Required behaviour |
|---|---|
| Corrupt or oversize file | Reject with a specific message; app stays usable |
| Ambiguous date or number format | Choose per rules, add a visible warning |
| Metric unavailable | Show it as unavailable with the missing role; never crash |
| Empty filter result | Friendly empty state |
| Validator rejects SQL | Repair loop (max 2), then "I could not answer this reliably" plus the reason |
| Query timeout | Message suggesting a narrower question; connection remains usable |
| Candidates disagree | Show best answer with **Unverified** badge |
| LLM unavailable, invalid JSON, rate limit | Backoff/retry; then offline behaviour or template narrator; Dashboard unaffected |
| Grounding failure twice | Template narrator for that section, badge "Template-written" |
| Token budget exceeded | Stop LLM calls; explain; everything else keeps working |
| Unexpected exception | "Something went wrong (ref: id)" and a logged traceback |

---

## 10. Non-Functional Targets

| Metric | Target |
|---|---|
| Upload → dashboard (demo-size data) | ≤ 10 s compute; ≤ 3 clicks |
| Slicer refresh (≤ 1M rows) | ≤ 2 s |
| Q&A end to end | ≤ 15 s p95 (live model) |
| Metric parity vs pandas | 100% within 1e-6 |
| Planted patterns detected | ≥ 7/8 per dataset |
| PII in audited payloads | 0 |
| Ungrounded numbers shown in AI-written cards | 0 |
| Golden accuracy of a live model | Measured and reported; targets: ≥ 90% cloud, ≥ 75% local (targets, not promises) |

---

## 11. Test Matrix (`tests/`)

`test_config.py`, `test_golden_sql.py`, `test_ingest_cleaning.py`, `test_pii.py`, `test_roles.py`, `test_relationships.py`, `test_flatten.py`, `test_metrics_parity.py`, `test_timeintel.py`, `test_sql_validator.py`, `test_executor_timeout.py`, `test_egress.py`, `test_audit_bytes.py`, `test_nl2sql_voting.py`, `test_offline_mode.py`, `test_filters_injection.py`, `test_kpi_parity.py`, `test_detectors_planted.py`, `test_detectors_boring.py`, `test_grounding.py`, `test_narrator_fallback.py`, `test_cloud_suppression.py`, `test_chaos_llm.py`, `test_single_litellm_import.py`, `test_app_smoke.py`. Use fixtures for tiny in-memory datasets and a `FakeProvider` with scriptable behaviour. Tests must run offline in under 3 minutes total.

---

## Appendix A — Demo datasets (exact)

**Retail** (`data/demo/retail/`), 2024-10-01 → 2026-09-30, about 120k sales rows.
- `customers(customer_id, name, email, phone, city, signup_date, segment[Consumer|Business])`, about 6,000 rows.
- `products(product_id, product_name, category[Electronics|Home & Garden|Apparel|Grocery|Sports|Beauty], unit_cost)`, about 120 rows.
- `stores(store_id, store_name["Store 01".."Store 08"], region[North|South|East|West])`.
- `sales(sale_id, sale_date, store_id, product_id, customer_id, channel[online|in-store], quantity, amount, discount, cost_amount, payment_status[paid|refunded|pending])`.
- Fact table `sales`; flat view columns after flattening: `sale_id, sale_date, store_id, product_id, customer_id, channel, quantity, amount, discount, cost_amount, payment_status, product_name, category, unit_cost, store_name, region, city, signup_date, segment` (customer `name/email/phone` dropped as PII).
- Roles: `date=sale_date, amount=amount, cost=cost_amount, quantity=quantity, customer_id=customer_id, order_id=sale_id, status=payment_status`.
- Planted patterns: **R1** in 2026-09, Store 03 revenue −40% vs its prior level, making total revenue fall about 8% (trend + driver: `store_name=Store 03`). **R2** daily revenue spike ×4 on 2026-08-12 and drop to ×0.1 on 2026-07-19 (anomaly). **R3** `Electronics` ≈ 48% of revenue (concentration). **R4** `online` average order value ≈ 2.6× `in-store` (segment_gap on `channel`). **R5** customer churn rate ≈ 25% baseline rising to ≈ 45% in 2026-09 (churn). **R6** `discount` column 30% null (data_quality). **R7** `Home & Garden` last-3-months growth YoY ≈ +45% vs overall ≈ +5% (segment_momentum). **R8** `gross_margin_pct` falls about 4 points over the last 3 months via rising cost (trend on a percent metric).

**Bank** (`data/demo/bank/`), same period, about 150k transaction rows.
- `customers(customer_id, full_name, phone, city, segment[standard|premium], join_date)`, about 8,000 rows.
- `branches(branch_id, branch_name["Branch 01".."Branch 10"], region)`.
- `transactions(txn_id, txn_date, customer_id, branch_id, channel[mobile|card|branch|atm], txn_type[transfer|payment|withdrawal|deposit], amount, status[completed|failed|reversed], fee)`.
- Fact table `transactions`; flat columns: `txn_id, txn_date, customer_id, branch_id, channel, txn_type, amount, status, fee, branch_name, region, city, segment, join_date` (`full_name`, `phone` dropped).
- Roles: `date=txn_date, amount=amount, customer_id=customer_id, order_id=txn_id, status=status` (no `cost`, no `quantity`, so unavailable metrics must degrade gracefully and appear in Next steps).
- Planted patterns: **B1** Branch 07 volume −35% in 2026-09 (trend + driver). **B2** transaction count burst ×3 on 2026-08-27 (anomaly). **B3** top-3 branches ≥ 60% of volume (concentration). **B4** failed-transaction rate 2% → 6% in 2026-09, `mobile` about 3× other channels (trend on percent metric + segment_gap). **B5** customer churn rise last month (churn). **B6** `fee` 25% null (data_quality). **B7** `mobile` volume YoY ≈ +60% vs overall ≈ +8% (segment_momentum). **B8** `premium` average ticket ≈ 4× `standard` (segment_gap on `segment`).
- Golden questions: 20 per dataset written against the flat columns above.

## Appendix B — Role synonyms (priority order)

`date`: `date, order_date, sale_date, txn_date, transaction_date, created_at, timestamp, day, period`
`amount`: `amount, revenue, sales, total, total_amount, price_total, value, sum, turnover`
`cost`: `cost, cost_amount, cogs, expense, unit_cost_total`
`quantity`: `quantity, qty, units, count_items`
`customer_id`: `customer_id, client_id, cust_id, customer, client, user_id, account_id`
`order_id`: `order_id, sale_id, txn_id, transaction_id, invoice_id, id`
`status`: `status, state, payment_status, txn_status`
Match on the normalized (lowercase snake_case) name: exact match first, then contains-match.

## Appendix C — Metric packs (exact expressions)

`generic` (primary `row_count`): `row_count = COUNT(*)`; `total_amount = SUM({amount})` [requires amount]; `avg_amount = AVG({amount})`; `distinct_customers = COUNT(DISTINCT {customer_id})`.

`retail` (primary `revenue`): `revenue = SUM({amount})`; `orders = COUNT(DISTINCT {order_id})`; `avg_order_value = SUM({amount}) / NULLIF(COUNT(DISTINCT {order_id}), 0)` (unit currency, additive false); `units = SUM({quantity})`; `gross_profit = SUM({amount}) - SUM({cost})` [requires cost]; `gross_margin_pct = 100.0 * (SUM({amount}) - SUM({cost})) / NULLIF(SUM({amount}), 0)` (unit percent, up); `active_customers = COUNT(DISTINCT {customer_id})`. Revenue counts only rows where `{status}` = `paid` when that value exists: use `SUM({amount}) FILTER (WHERE {status} = 'paid')` with `requires_values: {status: [paid]}`; if not, fall back to the generic `total_amount`.

`bank` (primary `txn_volume`): `txn_count = COUNT(*)`; `txn_volume = SUM({amount}) FILTER (WHERE {status} = 'completed')` [requires_values status: completed]; `avg_ticket = AVG({amount}) FILTER (WHERE {status} = 'completed')`; `failed_rate_pct = 100.0 * COUNT(*) FILTER (WHERE {status} = 'failed') / NULLIF(COUNT(*), 0)` (percent, good_direction down); `active_customers = COUNT(DISTINCT {customer_id})`; `fee_income = SUM(fee)` [requires a column literally named `fee`].

## Appendix D — Malicious SQL corpus categories (≥ 40 statements)

DDL (`DROP/CREATE/ALTER`); DML (`INSERT/UPDATE/DELETE`); multiple statements with `;`; comment-hidden second statement; `COPY ... TO`; `ATTACH`; `INSTALL/LOAD`; `PRAGMA`; `SET`; file and URL readers (`read_csv`, `read_parquet`, `glob`, `read_text`); `getenv`; system tables (`information_schema`, `duckdb_tables()`); other table names; schema-qualified names; quoted-identifier tricks; `UNION` with system tables; `EXPLAIN ANALYZE` wrapping DML; recursive CTE explosion (timeout); `SELECT` with side-effect functions; extremely long statements (length limit 5,000 characters).

## Appendix E — Final Report the agent must print at the end

1. Tree of created files. 2. Test summary (counts, duration). 3. Table of the 16 planted patterns (detected yes/no). 4. Eval result if a key was available (else "not run"). 5. Measured performance and memory numbers. 6. List of assumptions and known limitations. 7. Exact commands to: run tests, run the app locally, build and run Docker, deploy to Render. 8. Any deviation from this specification and why.
