# Finalyzer v3.1-Z final report

Date: 2026-10-09. Spec: `English.md`. Base measurements: `docs/V31Z_BASELINE.md`.
Verified facts: `ASSUMPTIONS.md` (v3.1-Z section). Honest gaps: `KNOWN_LIMITATIONS.md`.

## (a) Verified Render Free and provider limits

Checked 2026-10-09 against official docs (R13). Summary:

| Item | Value |
| --- | --- |
| Render Free | 0.1 CPU, 512 MB RAM, sleep after 15 min, ephemeral disk, 750 hours/month |
| Haiku-class model | `claude-haiku-5-5` (active). `claude-3-5-haiku-*` retired 2026-02-19 |
| Grok models | not chosen (no key to list `/v1/models`; `GROK_MODEL_*` stay empty) |
| Anthropic SDK | 1.x, no `temperature` |
| xAI chat | `POST /v1/chat/completions` at `https://api.x.ai/v1` |

Sources are listed in `docs/V31Z_BASELINE.md`.

## (b) Chosen model names

Free preset (`.env.free.example`, `render.yaml`):

- `CLAUDE_MODEL_FAST/MAIN/DEEP=claude-haiku-5-5`
- `GROK_MODEL_FAST/MAIN/DEEP=` (empty until `scripts/grok_smoke.py` lists models)
- Chain: `anthropic,grok,offline` (last link always offline)

## (c) 512 MB container test

Measured 2026-10-09 (`docker run --memory=512m -e RESOURCE_PROFILE=free -e LLM_MODE=offline`):

| Step | Result |
| --- | --- |
| Idle (`docker stats`) | 175 MiB / 512 MiB |
| Uvicorn RSS (`psutil`, idle) | 282 MB |
| After retail demo + build + insights | 379 MiB / 512 MiB, no OOM |
| `/health` | 200, 18 ms |
| Ingest (retail demo) | 2.2 s |
| Build + autopilot | 2.6 s |
| Insights cards | 3 (free cap) |

`docker compose up --build` (Postgres + app): `/health` 200, `/health/ready` ready, template mode.

`pandas` remains a runtime dependency (see `KNOWN_LIMITATIONS.md`). Windows dev idle RSS was 225 MB (`docs/V31Z_BASELINE.md`).

## (d) Provider-chain tests

`tests/test_providers_http.py` covers Anthropic and Grok mocked transport: forced-tool
parsing, 429/5xx retry, timeout, circuit, streaming, budget stop, chain fallthrough, no
secrets in logs. `tests/test_no_litellm.py` asserts LiteLLM is gone.
`tests/test_canary_providers.py` runs canaries in Anthropic and Grok shapes.

Live smoke: `scripts/claude_smoke.py` and `scripts/grok_smoke.py` were **not run** (no key).

## (e) Budgets (free / full)

| Action | free target | measured here |
| --- | --- | --- |
| `/health` | ≤ 50 ms | 18 ms in the 512 MB container; no heavy imports (`backend/tests/test_health_v31z.py`) |
| HTTP request | ≤ 30 s | provider timeout `http_timeout_s=30`; long work is a job + SSE heartbeat 15 s |
| Insights first paint | frames, on demand | GET `/insights` returned 3 frame cards after a 2.6 s build |
| Ask LLM-path (free) | Unverified | one candidate; reason shown (`tests/test_nl2sql_voting.py`) |
| Process RSS peak | ≤ 400 MB | 379 MiB after demo + insights in `--memory=512m` |

## (f) Smoke scripts

| Script | Result |
| --- | --- |
| `scripts/claude_smoke.py` | not run (no `ANTHROPIC_API_KEY`) |
| `scripts/grok_smoke.py` | not run (no `XAI_API_KEY`) |
| `scripts/eval_golden.py --model offline` | harness; not a model-quality figure |
| Haiku gold eval | **inconclusive / not run** — no live key. Do not quote insight quality in the pitch (gate: owner rating ≥ 4.0 required) |

## (g) Commands

Local API (backend venv):

```bash
backend\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

Tests (both profiles, R14):

```bash
.venv\Scripts\python.exe -m pytest tests -q
backend\.venv\Scripts\python.exe -m pytest tests -q
$env:RESOURCE_PROFILE='free'; .venv\Scripts\python.exe -m pytest tests/test_profile.py tests/test_ingest_v31z.py tests/test_nl2sql_voting.py -q
$env:RESOURCE_PROFILE='full'; .venv\Scripts\python.exe -m pytest tests/test_profile.py -q
cd frontend; npm test; npm run typecheck; npm run lint
python scripts/check_js_budget.py
```

Render: blueprint `render.yaml` (`plan: free`, health `/health`). Copy `.env.free.example`.
Keep `PROVIDER_CLEARANCE` at `synthetic_only` until the owner confirms provider terms in writing.
