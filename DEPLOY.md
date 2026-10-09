# Deploying Finalyzer

The product is one Docker web service that serves the API and the built SPA.
`render.yaml` describes the Render blueprint. `Dockerfile` builds a non-root image
with **one Uvicorn worker**. Streamlit is not in the image.

## Generate keys

```bash
python -c "import secrets,base64; print('APP_SECRET_KEY=' + secrets.token_urlsafe(48)); print('APP_MASTER_KEY=' + base64.b64encode(secrets.token_bytes(32)).decode())"
```

`APP_MASTER_KEY` is required only for `STORAGE_MODE=persistent`.

## Local Docker Compose

```bash
export APP_SECRET_KEY=...   # from the command above
docker compose up --build
```

Open http://localhost:8000/health then http://localhost:8000/setup.
Postgres is required by the compose file (accounts). Dataset files stay ephemeral
unless you add a volume and set `STORAGE_MODE=persistent`.

On-prem TLS: set `SITE_ADDRESS` and run `docker compose --profile tls up`.
Caddy in `deploy/Caddyfile` terminates TLS and proxies to the app.

## Render

1. Push the repository. Confirm `.env` is not in git (it is gitignored).
2. New > Blueprint, select the repo. `render.yaml` proposes the web service.
3. Set secrets in the dashboard (`sync: false` vars): `APP_SECRET_KEY`,
   `APP_MASTER_KEY` (if persistent), `DATABASE_URL` (Render Postgres URL),
   optional `ANTHROPIC_API_KEY` / `XAI_API_KEY`.
4. Health check is **`/health`** (keep-alives must not hit `/health/ready`).
5. Default: `plan: free`, `STORAGE_MODE=ephemeral`, `PRIVACY_MODE=demo`, `LLM_MODE=offline`.
   Ephemeral disk means uploaded datasets disappear on sleep or redeploy.
   Persistent mode needs a disk **and** `APP_MASTER_KEY` (not available on the free plan).
6. Copy `.env.free.example` for the intended env list. Keep `PROVIDER_CLEARANCE` at
   `synthetic_only` until the owner confirms each provider's current data terms in writing.

## Smoke test

- [ ] `/health` returns ok within 50 ms (warm).
- [ ] `/setup`: create the first admin.
- [ ] Home: load the retail demo → dashboard tiles appear.
- [ ] Insights: cards with numbers.
- [ ] Ask: a suggested Uzbek question, badge Tasdiqlangan.
- [ ] Privacy: bytes sent outside is 0 in offline mode.

## Memory

`render.yaml` uses **`plan: free`** (512 MB). The free profile targets process peak
≤ 400 MB (`RESOURCE_PROFILE=free`, DuckDB `max_memory=256MB`, sequential ingest).
Measured 2026-10-09 in `docker run --memory=512m`: idle **175 MiB**, after the
retail demo + insights **379 MiB**. Windows dev idle RSS (pandas imported) was 225 MB.

```bash
docker build -t finalyzer .
docker run -d --name fz --memory=512m -p 8000:8000 \
  -e APP_ENV=production -e RESOURCE_PROFILE=free -e LLM_MODE=offline \
  -e APP_SECRET_KEY=... finalyzer
python scripts/mem_check.py --url http://127.0.0.1:8000/health
docker stats fz --no-stream
```

Older v2 numbers (not a v3.1-Z guarantee): idle ~70 MiB; demo path peak ~355 MiB;
1M rows ~754 MiB. Those 1M-row figures need the **full** profile, not Render Free.

## Keep-alive (optional)

Render Free sleeps after 15 minutes without inbound traffic. An external ping
must hit **`GET /health` only** (not `/health/ready`). Check the ping service's
terms before enabling one. Cold start of the app itself was ~4.6 s locally;
Render's own wake is about one minute.

## Cold start

```bash
python scripts/measure_idle.py --path /health
```

## Rollback

Render dashboard → Events → last good deploy → Rollback, or revert the git commit.

## No LLM key

The app stays up: insights use frames, Ask uses the deterministic router and the
golden offline answers. Set `LLM_MODE=offline` and clear `LLM_API_KEY` if a key leaked
(and revoke it at the provider).
