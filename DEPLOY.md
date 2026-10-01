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

Open http://localhost:8000/healthz then http://localhost:8000/setup.
Postgres is required by the compose file (accounts). Dataset files stay ephemeral
unless you add a volume and set `STORAGE_MODE=persistent`.

On-prem TLS: set `SITE_ADDRESS` and run `docker compose --profile tls up`.
Caddy in `deploy/Caddyfile` terminates TLS and proxies to the app.

## Render

1. Push the repository. Confirm `.env` is not in git (it is gitignored).
2. New > Blueprint, select the repo. `render.yaml` proposes the web service.
3. Set secrets in the dashboard (`sync: false` vars): `APP_SECRET_KEY`,
   `APP_MASTER_KEY` (if persistent), `DATABASE_URL` (Render Postgres URL),
   optional `LLM_MODEL` / `LLM_API_KEY`.
4. Health check is **`/healthz`** (not the old Streamlit `/_stcore/health`).
5. Default: `STORAGE_MODE=ephemeral`, `PRIVACY_MODE=demo`, `LLM_MODE=offline`.
   Ephemeral disk means uploaded datasets disappear on sleep or redeploy.
   Persistent mode needs a disk **and** `APP_MASTER_KEY`.

## Smoke test

- [ ] `/healthz` returns ok.
- [ ] `/setup`: create the first admin.
- [ ] Home: load the retail demo → dashboard tiles appear.
- [ ] Insights: cards with numbers.
- [ ] Ask: a suggested Uzbek question, badge Tasdiqlangan.
- [ ] Privacy: bytes sent outside is 0 in offline mode.

## Memory (plan size = peak × 1.3)

Measured on the v2 image (see `KNOWN_LIMITATIONS.md`); v3 adds the SPA in memory
of the same process. Until a fresh `docker stats` run is stored, treat these as
**lower bounds** and size up:

| Situation | Memory (v2, recorded) |
| --- | --- |
| Idle container | about 70 MiB |
| Demo path (retail, insights, one question) | peak about 355 MiB |
| 1,000,000 fact rows (`scripts/perf_check.py`) | peak 754 MiB |

Safe estimates: demo path **≥ 553 MiB**; 1M rows **≥ 1.1 GiB**.
`render.yaml` uses `plan: standard` (2 GB). Lower `MAX_ROWS` on a smaller plan.
This is not a capacity guarantee.

To remasure:

```bash
docker build -t finalyzer .
docker run -d --name fz -p 8000:8000 -e APP_ENV=production -e APP_SECRET_KEY=... -e LLM_MODE=offline finalyzer
docker stats fz --no-stream
```

## Rollback

Render dashboard → Events → last good deploy → Rollback, or revert the git commit.

## No LLM key

The app stays up: insights use frames, Ask uses the deterministic router and the
golden offline answers. Set `LLM_MODE=offline` and clear `LLM_API_KEY` if a key leaked
(and revoke it at the provider).
