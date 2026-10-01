# Finalyzer

Private, Uzbek-first analytics: upload CSV or Excel, get a dashboard, plain-language
insights, and answers to questions. Numbers come from DuckDB. The model never invents
figures and never writes SQL for KPIs.

v3 is a FastAPI API plus a React SPA. The v2 Streamlit UI remains at `app/` for
regression tests; it is **not** in the Docker image.

## Prerequisites

- Python 3.12
- Node 22+
- Optional: Docker, PostgreSQL, an LLM API key (without one the app runs offline)

## Setup

Windows (PowerShell):

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.lock.txt
py -3.12 -m venv backend\.venv
backend\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
cd frontend; npm ci; cd ..
Copy-Item .env.example .env
```

Generate signing keys and put them in `.env`:

```powershell
python -c "import secrets,base64; print(secrets.token_urlsafe(48)); print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

macOS / Linux: the same commands with `python3.12` and `source .venv/bin/activate`.

Never commit `.env`.

## Run locally (two processes)

Backend (from `backend/`):

```bash
uvicorn app.main:create_app --factory --reload --port 8000
```

Frontend:

```bash
cd frontend
npm run dev
```

Open http://localhost:5173. The Vite proxy forwards `/api` to the API.

Or run API + built SPA from one origin after `npm run build` in `frontend/`:

```bash
cd backend
uvicorn app.main:create_app --factory --port 8000
```

Open http://localhost:8000. First visit: `/setup` (or `/first-run`) to create the admin.

## Tests

```bash
# v2 engine + Streamlit (from the repository root)
.venv\Scripts\python.exe -m pytest tests
ruff check core tests app scripts

# v3 API (from backend/)
backend\.venv\Scripts\python.exe -m pytest

# v3 UI
cd frontend
npm test
npm run typecheck
npm run lint
npm run build
python ..\scripts\check_contrast.py
python ..\scripts\check_js_budget.py
npx playwright test
```

The suites need no network and no API key.

## Demo data

```bash
python scripts/generate_demo_data.py
```

Writes retail, bank, sales and statistics CSVs under `data/demo/`.

## Docker

```bash
export APP_SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
docker compose up --build
```

The app listens on port 8000 (`/healthz`). Streamlit is not in the image.
TLS example: `docker compose --profile tls up` with `deploy/Caddyfile`.

Render: see `DEPLOY.md`. Health check path is `/healthz`.

## Privacy modes (`PRIVACY_MODE`)

| Mode | Model | What may leave the machine |
| --- | --- | --- |
| `demo` | cloud | synthetic data only (the demo datasets) |
| `cloud` | cloud | schema and aggregates; no result rows |
| `local` | Ollama (`LLM_API_BASE`) | nothing |

`LLM_MODE=offline` uses templates and the golden demo questions only.

Read `KNOWN_LIMITATIONS.md` and `ASSUMPTIONS.md` before relying on the output.
Guides: `docs/USER_GUIDE_uz.md`, `docs/USER_GUIDE_en.md`, `docs/ADMIN_GUIDE.md`, `docs/SECURITY.md`.
