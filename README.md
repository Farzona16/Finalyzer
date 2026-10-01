# Finalyzer

Upload tabular data (CSV or Excel), check how it was read, and get a private interactive
dashboard, insights whose numbers are checked against the data, and answers to questions in
plain English. Every number on screen comes from a DuckDB query; an AI model only writes text
and SQL, and its output is validated before anything is shown.

## Prerequisites

- Python 3.12
- Optional: Docker (to run the container), an LLM API key (without one the app runs in
  offline mode and answers only the built-in demo questions)

## Setup

Windows (PowerShell):

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.lock.txt
Copy-Item .env.example .env
```

macOS / Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock.txt
cp .env.example .env
```

`requirements.txt` holds the loose ranges; `requirements.lock.txt` holds the exact versions
that were tested. Edit `.env` to set the privacy mode, model and key (see the comments in
`.env.example`). Never commit `.env`.

## Run the tests and the linter

```bash
ruff check .
pytest
```

The suite needs no network and no API key.

## Generate demo data

```bash
python scripts/generate_demo_data.py
```

This writes two deterministic synthetic datasets (retail and bank, with planted patterns) to
`data/demo/`. Running it twice gives identical files.

## Start the app

```bash
streamlit run app/main.py
```

Open http://localhost:8501. Choose a demo dataset in the sidebar or upload your own files.

### Privacy modes (`PRIVACY_MODE`)

| Mode | Model | What may leave the machine |
| --- | --- | --- |
| `demo` | cloud | synthetic data only (the demo datasets) |
| `cloud` | cloud | schema and aggregates; no result rows; category values masked unless `SEND_CATEGORY_VALUES=true` |
| `local` | Ollama (`LLM_API_BASE`) | nothing |

With `LLM_MODE=offline`, or when there is no key outside local mode, the app uses templates
and the golden demo questions only.

## Run with Docker

```bash
docker build -t finalyzer .
docker run --rm -p 8501:8501 --env-file .env finalyzer
```

The container listens on `$PORT` (default 8501) and runs as a non-root user. The demo
datasets are generated while the image is built. To run fully offline:

```bash
docker run --rm -p 8501:8501 -e LLM_MODE=offline finalyzer
```

See `DEPLOY.md` for Render.

## Evaluate the question answering

```bash
python scripts/eval_golden.py --model offline                      # must give 40/40
python scripts/eval_golden.py --model provider/model-name          # uses LLM_API_KEY from .env
python scripts/check_payloads.py exported_audit.jsonl              # scan an exported audit log
python scripts/perf_check.py --json reports/perf.json              # timings on 1,000,000 rows
```

Reports are written to `reports/`. The model name must be one your key can use; a run where
the provider rejects or throttles the calls is not an accuracy measurement.

## Layout

```
app/       Streamlit UI (tabs, sidebar, theme, texts)
core/      ingest, semantic model, DuckDB layer, dashboard queries, insights, NL->SQL, LLM gateway
scripts/   demo data, evaluation, payload scan, performance check
tests/     pytest suite
reports/   generated reports (planted patterns, evaluation, performance)
```

Read `KNOWN_LIMITATIONS.md` before relying on the output and `ASSUMPTIONS.md` for the
decisions made while building.
