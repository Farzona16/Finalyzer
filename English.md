# FINALYZER v3.1-Z: Zero-Cost Infrastructure and Multi-Provider AI

**Owner:** Farzonaxon · **Version:** 3.1-Z · **Base:** Finalyzer v3.1 Quality Upgrade Spec (WP1–WP9, phases V0–V12)

## 0. Document status and new rules

This document sits on top of the v3.1 base spec and extends it. **If the two conflict, this document wins.** Rules R1–R11, decisions D1–D38 and defects K1–K8 from v3.1 stay in force. The goal is to build, test and deploy to Render Free with **no paid services**; the only AI spend allowed is free credits the owner already holds.

**R12. Zero-cost rule.** No feature may require a paid service. With no AI key, exhausted credits, an exceeded budget or a provider outage, the system switches automatically to `OfflineProvider` and template (frame) mode. The app never stops working.

**R13. External facts are verified.** Render Free limits, Claude and Grok free-credit terms and model names change over time. The agent verifies them at build time against official docs and the provider model list, and records the result in `ASSUMPTIONS.md`. No figure or model name is written from memory (R7).

**R14. Two-profile rule.** Every change is tested in both profiles: `free` and `full`.

---

## 1. Resource profiles

`RESOURCE_PROFILE=free|full`. If unset, the container memory limit (cgroup) is read at startup; 768 MB or less selects `free`.

| Parameter | free (Render Free) | full |
| --- | --- | --- |
| RAM target | process peak ≤ 400 MB | ≥ 2 GB |
| `INGEST_WORKERS` | 1 (sequential) | 4 |
| `MAX_FILE_MB` / `MAX_TOTAL_MB` / `MAX_FILES` | 10 / 30 / 10 | 50 / 300 / 30 |
| `MAX_ROWS` (total) | 500,000 | env |
| DuckDB `max_memory` / `threads` | 256MB / 1 | 50% of RAM / CPU count |
| `ASK_CANDIDATES` | 1 | 2 |
| Insight precompute | `on_demand` (first view) | `after_build` |
| Analyst Agent | ≤ 3 findings, ≤ 2 rounds, ≤ 6 calls, wall time ≤ 45 s | ≤ 5 findings, ≤ 3 rounds, ≤ 12 calls, 20 s |
| Models | all Haiku-class | fast=Haiku, main=Sonnet, deep=Opus |
| Tile query parallelism | 2 | by CPU count |
| Tile cache | LRU ≤ 64 MB | env |
| Token budgets | conservative (section 3.4) | env |

The profile is read in **one** place, `core/runtime/profile.py`. Other modules use the numbers derived from it, never the profile name.

---

## 2. Infrastructure for Render Free

### 2.1 Memory shield (512 MB)

- At startup: `SET max_memory='256MB'; SET threads=1; SET temp_directory='/tmp/duck'; SET preserve_insertion_order=false;`. Values are computed dynamically from the profile and the cgroup limit.
- CSV/TSV are streamed through DuckDB `read_csv`; Excel via `openpyxl` read-only mode; `.xls` via `xlrd`. `pandas` is a dev/test dependency only (not in the runtime image).
- `INGEST_WORKERS=1` is the forced default; in a memory-restricted environment the parallel path falls back to sequential automatically.
- **Sampling:** if one file's estimated memory exceeds the threshold, a deterministic (seeded) sample is taken. Every number in the app then carries a **Based on a sample (X%)** badge and no claims are made about exact totals (R11 holds).
- **Memory watchdog:** RSS is checked between steps; above 400 MB no new file is accepted, caches are cleared, and a plain-language explanation is shown.
- One process (`uvicorn`, 1 worker); idle RSS ≤ 150 MB.

### 2.2 Time limits (100 s gateway)

- No HTTP request waits longer than 30 s. Long work (ingest, Analyst Agent, ERD rebuild, Ask LLM path) runs as a **background job**: `POST` returns a `job_id` immediately; status streams over SSE with a heartbeat every 15 s, with `GET /jobs/{id}` polling as fallback.
- Jobs run in-process (`asyncio`), no queue service. After a restart an unfinished job becomes **interrupted** and a Restart button appears.
- Identical requests are merged with single-flight.

### 2.3 Health and cold start

- `GET /health`: dependency-free, imports no heavy modules, returns `200 OK` at once (≤ 50 ms). Keep-alive pings hit only this route.
- `GET /health/ready`: DB, provider and kill-switch state (not for pings).
- The app must work fully **after a cold start**. While the server wakes, the UI shows a Server is waking up state and retries for up to 60 s. Cold start ≤ 40 s (excluding Render's own wake time).
- An external keep-alive (free cron or ping service) is optional; the owner checks that using it fits the service's terms.

### 2.4 Ephemeral disk

The file system is treated as temporary: data, the DB and settings can disappear on restart or redeploy.

- Demo datasets are regenerated from seeds at every start.
- Privacy settings start from safe defaults (`strict`; kill-switch state from `KILL_SWITCH_DEFAULT`); a value stored in the DB wins if present.
- The UI always shows: **Data is stored temporarily**. Durable storage through an external DB (`DATABASE_URL`) is **optional**.

### 2.5 Packaging and CI

- `litellm` is removed. Minimal runtime dependencies: `fastapi`, `uvicorn`, `duckdb`, `openpyxl`, `xlrd`, `anthropic`, `httpx`, `pydantic`. The frontend is built in CI and served by FastAPI as static files (one service).
- CI uses the free GitHub Actions tier. Live LLM tests run manually only, with a token cap.

---

## 3. Provider architecture (extends D24)

### 3.1 Interface and capability flags

Each `LLMProvider` declares `capabilities`: `forced_tool`, `tool_loop`, `streaming`, `cache_markers`, `max_context`. Logic depends on flags, not provider names. Four providers:

1. **AnthropicProvider**: official `anthropic` SDK. All v3.1 section 3.2 requirements apply (forced tool, cache blocks, streaming, retries, circuit breaker).
2. **OpenAICompatProvider (Grok)**: `httpx`, base URL `OPENAI_BASE_URL` (default `https://api.x.ai/v1`), key `XAI_API_KEY`. Forced tool through `tools` + `tool_choice`; the answer is read from `tool_calls[0].function.arguments` and re-validated with Pydantic. If the provider rejects forced tools, fall back to `response_format` (JSON schema); if that also fails, set `forced_tool=false` and move to the next provider in the chain. Streaming over SSE `data:` events. No cache markers (`cache_markers=false`); automatic caching is never relied on.
3. **OllamaProvider**: for `local` mode.
4. **OfflineProvider**: deterministic frames, router, golden answers.

Anthropic and Grok share `HttpProviderBase`: timeout, retry (backoff + jitter, `retry-after`), circuit breaker, budgets, audit. Adding a provider means writing only a request/response mapping.

### 3.2 Model names

Model names come only from env and are never hard-coded (D25). At startup they are checked against the provider's model list; an unknown name logs a warning and the provider is skipped in the chain.

**Important:** `claude-3-5-haiku-latest` is an older-generation alias. Whether it still exists and works with the free credits **must be verified**. The default is the current Haiku-class `claude-haiku-5-5`, confirmed from the model list at build time. The owner may still set the old name through env, but the system warns if it is not in the list. Grok model names (`GROK_MODEL_FAST/MAIN/DEEP`) default to **empty**: the agent does not guess, it picks from `/v1/models` and records the choice in `ASSUMPTIONS.md`.

### 3.3 Chain and fallback

`LLM_PROVIDER_CHAIN=anthropic,grok,offline`. When a circuit opens, a budget runs out or an error occurs, the request moves to the next provider; the last is always `offline`. Every audit entry records the provider and model. For two-candidate SQL (D35), candidate B comes from a **different provider** when possible, because independent agreement is a stronger signal.

### 3.4 Token protection (for free credits)

- Conservative defaults: `SESSION_TOKEN_BUDGET=30000`, `DATASET_TOKEN_BUDGET=80000`, `DAILY_TOKEN_BUDGET=200000` (configurable). With `HARD_STOP_ON_BUDGET=true`, exceeding a budget means frames only plus a plain notice.
- A `max_tokens` cap per task. In the free profile prompts are compact: top 3 findings, truncated tool results, row-capped tables.
- If a provider returns credits exhausted or an authorization error, that provider is disabled for the session, the chain continues, and the UI shows AI temporarily off, template mode.
- The Privacy page shows tokens used and remaining. Prices are never hard-coded.

### 3.5 Task to model (free profile)

All tasks use a Haiku-class model (`fast = main`). The Deep analysis button is hidden when `DEEP` is not different from `main`. A Haiku-class model may write more plainly than Sonnet or Opus, so every checker (claim validator, SQL validator, parity) stays **unchanged**: lower writing quality means more claims are dropped, never that a wrong number is shown.

---

## 4. Privacy adjustments

- **Provider clearance (`PROVIDER_CLEARANCE`).** Admin setting per cloud provider: `synthetic_only` | `strict_ok` | `balanced_ok`. **The default for all is `synthetic_only`**: real customer data is allowed only after an admin confirms the provider's current data terms in writing. Free-credit or promotional programs may have terms different from the paid API; the owner reads them, and the UI makes no claim about retention or training use (R9).
- **Egress allowlist.** `EGRESS_ALLOWED_HOSTS` contains only the hosts of enabled providers (`api.anthropic.com`, `api.x.ai`). Any other host is blocked.
- Kill switch, `strict/balanced/open` levels, sensitivity tiers, payload builders and injection defence follow v3.1 section 5 unchanged.
- **The canary suite runs per provider** (capturing fakes shaped like Anthropic and Grok) in every mode/level combination.

---

## 5. Work packages: changes in the free profile

| WP | Change |
| --- | --- |
| WP1 Provider | Section 3 above; Grok adapter; `claude_smoke.py` and `grok_smoke.py` |
| WP2 Analyst | Background job + SSE; free limits (section 1); `on_demand` precompute; cache key includes provider and model |
| WP3 Privacy | Provider clearance, allowlist, kill-switch startup default |
| WP4 Ingest | Free limits; sequential reading; sampling; `.ods` and `.zip` off in free; `.parquet` allowed via DuckDB; union detection kept |
| WP5 Relationships | Fewer candidates: approximate check on ≤ 200,000 distinct values per column, exact check only for top candidates; **the fan-out guard (`COUNT(*)`) is always exact** |
| WP6 Trust | Hypothesis parity tests in CI only; runtime `PARITY_SAMPLE=0`; sample badge (2.1) |
| WP7 Interactive BI | Tile batch uses 2 parallel cursors, query dedupe, LRU cache |
| WP8 Design | Light mode defaults on for mobile and low-memory devices; ≤ 3 blur layers; initial JS ≤ 350 KB |
| WP9 Latency | Per-profile budgets (section 6) |

---

## 6. Budgets (p95)

| Action | free | full |
| --- | --- | --- |
| Filter change to all tiles updated | ≤ 2.5 s (≤ 500k rows) | ≤ 1.0 s (≤ 1M) |
| Single tile query | ≤ 1 s | ≤ 400 ms |
| Page navigation (cached) | ≤ 300 ms | ≤ 150 ms |
| Insights first paint (frames) | ≤ 500 ms | ≤ 300 ms |
| Claude insights ready | ≤ 45 s, streamed, on demand | ≤ 25 s, background |
| Ask, router path | ≤ 2 s | ≤ 1.5 s |
| Ask, LLM path | first event ≤ 1.5 s, done ≤ 20 s (measured, not promised) | ≤ 1 s / ≤ 12 s |
| Upload to dashboard | 5 files × 50k rows ≤ 60 s | 12 files × 100k rows ≤ 60 s |
| Process RSS peak | ≤ 400 MB | measured |

If a target is missed, the real number and the cause go into `KNOWN_LIMITATIONS.md`.

---

## 7. Configuration

**FREE preset (`.env.free.example`):**

```
RESOURCE_PROFILE=free
LLM_MODE=cloud
LLM_PROVIDER=anthropic
LLM_PROVIDER_CHAIN=anthropic,grok,offline
ANTHROPIC_API_KEY=
CLAUDE_MODEL_FAST=claude-haiku-5-5
CLAUDE_MODEL_MAIN=claude-haiku-5-5
CLAUDE_MODEL_DEEP=claude-haiku-5-5
XAI_API_KEY=
OPENAI_BASE_URL=https://api.x.ai/v1
GROK_MODEL_FAST=
GROK_MODEL_MAIN=
GROK_MODEL_DEEP=
EGRESS_ALLOWED_HOSTS=api.anthropic.com,api.x.ai
PRIVACY_LEVEL=strict
PROVIDER_CLEARANCE=anthropic:synthetic_only,grok:synthetic_only
KILL_SWITCH_DEFAULT=off
INGEST_WORKERS=1
MAX_FILES=10
MAX_FILE_MB=10
MAX_TOTAL_MB=30
MAX_ROWS=500000
ASK_CANDIDATES=1
INSIGHT_PRECOMPUTE=on_demand
SESSION_TOKEN_BUDGET=30000
DATASET_TOKEN_BUDGET=80000
DAILY_TOKEN_BUDGET=200000
HARD_STOP_ON_BUDGET=true
PARITY_SAMPLE=0
VERIFY_PASS=false
```

**FULL preset (`.env.full.example`)** is filled with the values from v3.1 section 14. Secrets come only from the environment; the kill-switch state lives in the DB.

---

## 8. Phases (additions to the v3.1 phases)

**V0: Baseline.** v3.1 requirements plus `docs/V31Z_BASELINE.md`: idle RSS, dependency size, and the verified Render Free and provider limits (R13). **Accept:** all suites green.

**V1: Providers (WP1).** Anthropic + OpenAICompat (Grok) + Ollama + Offline, capability flags, chain, `litellm` removed. **Accept:** mocked-transport tests for both providers (forced-tool parsing, 429/5xx retry, timeout, circuit, streaming, budget stop, chain fallthrough, no secrets in logs); request/response samples taken from live docs; `offline` mode unchanged.

**V1b: Resource profile and memory shield.** `core/runtime/profile.py`, DuckDB limits, memory watchdog, background jobs + SSE + polling, `/health`, `/health/ready`, sample badge. **Accept:** inside `docker run --memory=512m`, 5 files × 50k rows load, the dashboard and offline insights open, RSS ≤ 400 MB, no OOM; with a slow fake provider no request exceeds 30 s; `/health` ≤ 50 ms.

**V2: Privacy v2.** v3.1 plus provider clearance and allowlist. **Accept:** canary, injection, kill-switch and builder tests green in both provider shapes; no real-data payload reaches a provider without clearance.

**V3: Smart ingest.** With free limits. **Accept:** all fixtures pass in a 512 MB container; one corrupt file does not stop the batch; the sample badge is visible.

**V4–V5: Relationships, Trust.** As in v3.1; relationship tests keep precision ≥ 0.95 and recall ≥ 0.90 with the free candidate pruning; zero fan-out.

**V6: Analyst Agent.** Background job, free limits, `on_demand`. **Accept:** v3.1 section 4.4 tests; a separate gold evaluation report for the Haiku configuration.

**V7: Ask v3.** Free: one candidate, so LLM-path answers always read Unverified with the reason shown; router-path answers read Verified. Full: two-candidate agreement (D35). **Accept:** v3.1 V7 plus both profiles.

**V8–V11: Interactive BI, Design, Dashboard UI, remaining pages.** As in v3.1; in free, Light mode defaults on, tile batch uses 2 cursors, and provider-state badges (credits exhausted, template mode) appear in the UI.

**V12: Free deploy and packaging.** `render.yaml` (free plan, health check path `/health`, env list), small Dockerfile, `docs/DEPLOY.md` (measured RSS, cold start, latencies), keep-alive instructions, `.env.free.example` and `.env.full.example`. **Accept:** all gates, `scripts/mem_check.py`, cold-start test, `docker compose up` smoke test, final report.

---

## 9. Quality gates: additions

| Gate | Threshold |
| --- | --- |
| 512 MB container test | no OOM, RSS ≤ 400 MB |
| Any HTTP request > 30 s | none, 100% |
| `/health` | ≤ 50 ms, no heavy imports |
| Provider chain | every fallthrough tested; last link always offline |
| Haiku configuration | claim validator: 0 unsupported numbers |
| Live evaluations | complete reports stored, token-capped; mostly-error runs labelled inconclusive |
| Pitch claims | insight quality quoted only if the owner's rating on the Haiku configuration is ≥ 4.0 |

---

## 10. Owner tasks (additional)

1. Get the Claude and Grok keys; check the remaining free credit amount and expiry; set a spend cap in the console if one exists.
2. Read each provider's current data terms (including the free-credit program's, if separate); keep `PROVIDER_CLEARANCE` at `synthetic_only` until you confirm them in writing.
3. Confirm Render Free's current limits (memory, sleep, disk, hours) on the official page.
4. Decide whether to use a keep-alive.
5. Rate 40 insights 1–5 on the Haiku configuration and write the gold insights.
6. The rest of the v3.1 owner list (uz\_review, real users, font choice, legal review) is unchanged.

---

## 11. Honest limitations (go into `KNOWN_LIMITATIONS.md`)

- A Render Free service sleeps after inactivity; the first request is slow.
- The disk is temporary: data can be lost; durable storage needs an optional external DB.
- Because CPU and RAM are limited, large files are processed from a sample and this is shown openly.
- Free credits can run out; the system then runs in template mode and no AI text appears.
- A Haiku-class model may write more plainly than Sonnet or Opus; the guarantee lives in the checkers, not in style.
- In the free profile, LLM-path answers carry the Unverified badge because there is no second candidate.
- Free-credit and promotional terms can change; no privacy or retention guarantee is written anywhere in the docs.

---

## 12. Final report: additions

Added to v3.1 Appendix B: (a) verified Render Free and provider limits with the date checked; (b) chosen model names and the list that confirmed them; (c) 512 MB test results (peak RSS, time); (d) provider-chain test results; (e) measurements against the free and full budgets; (f) `claude_smoke.py` and `grok_smoke.py` results or not run (no key); (g) exact commands to run locally and on Render.
