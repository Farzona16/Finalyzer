# v3.1-Z baseline (phase V0)

Measured on 2026-10-09 on the development laptop (Windows 11, Python 3.12.10), before any
v3.1-Z change. Re-measure inside the container with `scripts/mem_check.py` (phase V12).

## Test suites before v3.1-Z

| Suite | Result before fixes | Cause | Fix |
| --- | --- | --- | --- |
| `tests/` (engine + Streamlit) | 4 failed | The developer `.env` leaked into tests (`SQL_CANDIDATES=1`); the system prompt was run through value masking, so "business" became `val_15`; `docs/uz_review.md` was stale; one test expected old wording | `tests/conftest.py` ignores `.env`; the system prompt gets identifier masking only; doc regenerated; test updated |
| `backend/tests/` (API) | 1 failed | An upload with only empty named files reported `validation_failed` | Such an upload now reports `upload_invalid` |
| `frontend` (vitest, tsc, eslint) | green | - | - |

## Process and dependencies

| Item | Value | How |
| --- | --- | --- |
| Idle RSS (API + SPA, offline, SQLite) | 225 MB | `python scripts/measure_idle.py --path /healthz` (Windows, pandas/numpy/duckdb imported at startup) |
| Cold start to first `200` | 4.6 s | same script |
| Backend venv `site-packages` | 828 MB | includes test and lint tools |
| Largest packages | litellm 118 MB, scipy 109 MB, pyarrow 86 MB, pandas 62 MB, plotly 57 MB, streamlit 31 MB, numpy 31 MB | directory sizes |

The v3.1-Z targets are idle RSS ≤ 150 MB and process peak ≤ 400 MB in the free profile.

## Verified external facts (R13), checked 2026-10-09

### Render Free (https://render.com/docs/free)

- Compute plan `free`: 0.1 CPU / 512 MB RAM.
- Spins down after 15 minutes without inbound traffic; spin-up "takes about one minute".
- Ephemeral filesystem: changes are lost on redeploy, restart **and spin-down**. No persistent disks.
- 750 free instance hours per workspace per month; all free services are suspended when used up.
- Render may restart a free service at any time. No SSH, no one-off jobs, single instance.
- Free Render Postgres: 1 GB, expires 30 days after creation, no backups.
- Render may suspend a free service that initiates an "uncommonly high volume" of outbound traffic.

### Anthropic (https://docs.anthropic.com/en/docs/about-claude/models/overview and /model-deprecations)

- Current Haiku-class model: `claude-haiku-5-5` (active, retirement not sooner than 2027-10-07).
- `claude-3-5-haiku-20241022` was **retired on 2026-02-19**; requests to it fail. The
  `claude-3-5-haiku-latest` alias therefore does not work. The system warns and skips the
  provider when an unknown name is configured.
- Python SDK `anthropic` 1.12.1 (PyPI, 2026-10-09). From SDK v1.0, `temperature`, `top_p`
  and `top_k` are removed (passing them raises `TypeError`); Claude 4.7+ models return 400
  for non-default values. The provider never sends a temperature.
- The SDK 1.x transport is `httpx2` (`http_client: httpx2.Client`); tests inject
  `httpx2.MockTransport`.
- Free credits: the official pricing page says only "New users receive a small amount of free
  credits to test the API". The amount and expiry are not published there; third-party pages
  report about $5 that expires about 14 days after claiming. **The owner checks the console.**

### xAI / Grok (https://docs.x.ai)

- `POST /v1/chat/completions` is still documented (last updated 2026-09-02) as the
  "stateless, OpenAI-compatible predecessor of the Responses API". The adapter uses it, as the
  spec requires.
- Forced tool: `tool_choice: {"type": "function", "function": {"name": ...}}`; arguments arrive
  as a JSON string in `choices[0].message.tool_calls[0].function.arguments`.
- With streaming, a function call arrives whole in a single chunk.
- Models on the pricing page today: `grok-4.7`, `grok-4.6`, `grok-4.5`, `grok-4.3`,
  `grok-4.20-0309-reasoning`, `grok-4.20-0309-non-reasoning`, ... **No Grok model is chosen**:
  `GROK_MODEL_*` stay empty until `scripts/grok_smoke.py` lists `/v1/models` with the owner's
  key (no key was available at build time).
- Free credits: the earlier "up to $150/month" credit requires opting in to **data sharing**
  (xAI may train on inputs and outputs), a prior $5 spend, an eligible country, and the opt-in
  is irreversible. It is no longer described on docs.x.ai. This is why `PROVIDER_CLEARANCE`
  for Grok must stay `synthetic_only` unless the owner confirms the terms in writing.
