# Deploying Finalyzer to Render

The app is one Docker web service. `render.yaml` describes it; the `Dockerfile` builds it.

## 1. Before you start

- A GitHub account and a Render account.
- Optional: an LLM API key and a model name that the key can use. Test the pair first:
  `python scripts/eval_golden.py --model <model>` should get answers, not "rejected" errors.
  Providers retire model names (the `gemini-1.5-flash` name in an older `.env` was rejected),
  so prefer a current name.
- The demo datasets are synthetic and are built into the image. Do not point the public
  service at real personal data: `PRIVACY_MODE=demo` means "synthetic data only".

## 2. Steps

1. Push the repository to GitHub. `.env` is ignored by git and by `.dockerignore`; check that
   it is not in the commit.
2. In Render choose **New > Blueprint** and select the repository. Render reads `render.yaml`
   and proposes the `finalyzer` web service.
3. Fill in the three variables that have no value in `render.yaml` (they are declared with
   `sync: false`, so they are entered in the dashboard and never stored in git):
   - `LLM_MODEL`: LiteLLM model string, for example `provider/model-name`
   - `LLM_API_KEY`: the key (leave empty for offline mode)
   - `APP_PASSWORD`: optional shared password; empty means no password gate
4. Check the plan (see "Memory" below) and create the service. Render builds the image
   (about 4 minutes the first time) and waits for `/_stcore/health` to answer `ok`.
5. Open the service URL and run the smoke test.

## 3. Smoke test

- [ ] The page opens; the sidebar says which AI mode is active.
- [ ] Choose **Demo: Retail**: KPI cards and charts appear, no error box.
- [ ] Insights tab: press the button; cards appear, each with numbers and a badge.
- [ ] Ask tab: click a suggested question; the answer is marked Verified.
- [ ] Audit tab: "Bytes sent outside" matches what you expect (0 in offline mode).
- [ ] Choose **Demo: Bank**, then **Clear session**; the page returns to the start.
- [ ] If `APP_PASSWORD` is set, the page asks for it first.

## 4. Memory

Measured in the built image (Docker 29, Linux, Python 3.12, offline mode, one session):

| Situation | Memory |
| --- | --- |
| Idle container, server started, no visitor yet | about 67-70 MiB |
| One session on the demo path (retail, insights, one question, bank) | peak 355 MiB (process peak, `ru_maxrss`); container sampled at 299 MiB |
| One session with 1,000,000 fact rows (`scripts/perf_check.py`, includes building the table) | peak 754 MiB |

The session figures do not include the idle server, so a safe estimate is the session peak
plus about 70 MiB: roughly **425 MiB** for the demo path and **825 MiB** for a 1,000,000-row
dataset. Each additional visitor adds a session of their own.

The rule is: plan RAM should be at least peak x 1.3.

- Demo path: 425 x 1.3 = about 553 MiB. **Plans with 512 MB (free, starter) are below this
  and may be killed for running out of memory.**
- 1,000,000 rows: 825 x 1.3 = about 1,075 MiB. A 2 GB plan is enough for one or two
  visitors.

`render.yaml` therefore sets `plan: standard` (2 GB). It is a setting: change it there, and
also lower `MAX_ROWS` and `DUCKDB_MEMORY_LIMIT` if you must use a smaller plan.

To repeat the measurement:

```bash
docker build -t finalyzer .
docker run -d --name fz -p 8501:8501 -e LLM_MODE=offline finalyzer
docker stats fz --no-stream
docker exec -w /app -e PYTHONPATH=/app fz python scripts/perf_check.py
```

## 5. Rollback

In the Render dashboard open the service, choose **Events**, pick the last good deploy and
press **Rollback**. Or push a revert commit; `autoDeploy` redeploys it.

## 6. The LLM key fails or the quota is used up

The app keeps working: without a usable key it falls back to template-written insights and
answers only the built-in demo questions. To force this for everyone, set `LLM_MODE=offline`
in the Render dashboard (Environment) and redeploy; clear `LLM_API_KEY` if the key was leaked
(and revoke it at the provider). Users see a friendly message, never the provider's error.

## 7. Running the container locally

```bash
docker build -t finalyzer .
docker run --rm -p 8501:8501 --env-file .env finalyzer
```

The container reads `PORT` (default 8501), so `-e PORT=10000 -p 10000:10000` also works, as
on Render.
