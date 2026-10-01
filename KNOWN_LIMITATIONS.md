# Known limitations

This list is meant to be honest. Numbers come from files in `reports/` or from the commands
shown; nothing here is an estimate unless it says so.

## Product scope

- English only (interface, questions, insights).
- One fact table per dataset; other tables are joined to it as dimensions. Star-schema data
  with several fact tables must be prepared outside the app.
- No persistence: data, findings and the audit log live in the session and are lost when it
  ends. There are no user accounts; `APP_PASSWORD` is one shared password.
- Inactivity cleanup is best effort: it is whatever Streamlit does when a session ends.
  "Clear session" is the reliable way to drop data. Clearing also empties Streamlit's data
  cache, which is shared by all sessions, so other visitors recompute once.
- Uploads: CSV and `.xlsx`, up to `MAX_FILE_MB` (50) and `MAX_ROWS` (1,000,000). Excel
  formulas are read as values and never executed.

## Insights

- The trend detector compares monthly totals and does not adjust for the number of days in a
  month, so February to March (+10.7% from length alone) can be reported as growth on real data.
- The anomaly detector also flags a weak third day in the retail demo (2026-08-04) that was not
  planted; that is a false positive of the detector, limited to three anomalies per run.
- Grounding checks the digits in a text against the facts. Numbers written as words ("three")
  are not parsed, so a wrong number word would not be caught.
- Findings are evidence of a pattern in the data, not proof of a cause.

## Question answering (NL to SQL)

- Accuracy of the model-written SQL is measured, not guaranteed. The golden set has 40
  questions (20 retail, 20 bank).
  - Offline mode (no model): 40/40, report `reports/eval_offline_2026-09-30.json`. This only
    shows that the harness and the golden answers agree; it says nothing about a real model.
  - Live model, measured: `groq/openai/gpt-oss-120b` on the 20 retail questions passed 17/20
    (85%), report `reports/eval_groq-openai-gpt-oss-120b_2026-10-01.json`. The three misses
    returned a different result set than the golden answer. Some calls hit the provider's
    rate limit and were retried, which raised latency (mean 13 s per question). The 20 bank
    questions were not run with a live model, so no overall figure is claimed.
  - An earlier run with `gemini/gemini-flash-latest` is **not an accuracy figure**. One run was made with `gemini/gemini-flash-latest`
    (`reports/eval_gemini-gemini-flash-latest_2026-09-30.json`). The provider rate-limited
    the key after the first four questions, so 36 questions got no answer from the model.
    The file shows 4/40 because of that; it is **not an accuracy figure**. Of the four
    questions that were answered, all four were correct, which is too few to conclude
    anything. Re-run `scripts/eval_golden.py` with a key that has enough quota.
  - A model name in the local `.env` (`gemini/gemini-1.5-flash`) was rejected by the
    provider as retired. Model names need checking before every deployment.
- The app writes three candidate queries and uses the result that a majority of them agree
  on. It may refuse or ask for a clarification on questions a person would find easy.
- Offline mode answers only the built-in demo questions.

## Privacy and security

- `PRIVACY_MODE=cloud` sends schema and aggregates to the model provider; `SEND_CATEGORY_VALUES`
  can also send category values. Masking is the default, but that is a trade-off: a value such
  as a store name is sometimes needed for a useful answer. Findings based on fewer than
  `K_ANON_MIN` (5) rows are not sent.
- The payload scan recognises e-mail, phone and card numbers (with a Luhn check). Other kinds
  of personal data, such as names in free text, are not detected. Column-name hints and a value
  sample are used to drop likely personal columns, which is a heuristic.
- No formal security certification or penetration test. `pip-audit` on
  `requirements.lock.txt` (run 2026-09-30): no known vulnerabilities.
- XSRF protection stays enabled (`.streamlit/config.toml`).

## Performance (`scripts/perf_check.py`, 1,000,000 fact rows, `reports/perf.json`)

| Step | Seconds |
| --- | --- |
| Load, clean and profile | 5.8 |
| Propose and build flat views | 0.85 |
| All dashboard queries (unfiltered) | about 0.3 |
| Insight detectors, all rows | 1.1 |
| Apply slicers and run all dashboard queries (filtered) | about 0.13 |
| All interactive steps together | 0.43 (limit in the specification: 2 s) |

- The load time depends on the data. The text cleaning works once per distinct value of a
  column, so the demo table (which repeats its values) is fast. A table whose text columns are
  nearly all unique (ids, free text) gains much less; before this change the same demo table
  took about 36 s.
- Memory: see `DEPLOY.md`. Peak with 1,000,000 rows was 754 MiB in the container, so plans
  with 512 MB of RAM (including free tiers) are too small for that, and too small for the
  demo path with the safety margin.
- Free hosting tiers may sleep when idle, so the first visit after a pause is slow.
- The Docker image is about 1.4 GB because `requirements.lock.txt` also contains the test and
  lint tools and the LiteLLM dependencies.

## Tests

- The test suite is offline. Live-model behaviour is tested with a fake provider and with
  chaos tests that patch the provider call; it is not tested against a real provider in CI.
- Docker was built and run once on the development laptop; the Render deployment itself was
  not performed from here, so `DEPLOY.md` follows `render.yaml` but has not been tried on Render.

## KPI autopilot (phase U5)

- The classifier is measured on 12 fixtures written by the same author as the pack hints (retail, sales, bank, project, expenses, statistics, generic in Uzbek, Russian and English). 12 of 12 are right, but that says little about unseen real files; the figure must not be read as a field accuracy.
- The project and expenses packs have no demo dataset, so they are checked by structure tests only, not by a parity run against real rows.
- The expenses pack does not compute `spend_by_category_share` and `largest_category_share` as KPI tiles; the dashboard shows the same information as chart tiles.
- Sales "stalled deals" is relative to the latest date in the data, so an old export shows its own latest state as "current".
- The AI metric suggestions are only as good as the model; the DSL limits what can go wrong (no SQL, at most 5 filters, known columns and values only, no personal-data columns), and anything that fails the parity check is dropped, but a valid and boring metric can still be suggested.
- Heuristic Uzbek labels are templates (`core/autopilot/labels.json`) and have not been reviewed by a native speaker; they are listed in `docs/uz_review.md`.
- The record of what was sent to the AI during the autopilot is kept in memory and is lost on restart; the Privacy page that shows it arrives in phase U11.

## Insights v3 (phase U6)

- The outliers detector uses 3×IQR (the v2 rule), not the spec's 1.5×IQR, so only extreme tails are flagged. Left-skew is not reported. Boolean dimensions are left out of `group_difference`.
- `lost_reason` "37% empty" on the sales demo is structural (open and won deals have no lost reason), not a data defect.
- `outliers` and `correlation` on derived columns (for example amount vs cost_amount) can be trivial; retail and bank therefore run only the v2 detectors.
- The v2 `segment_momentum` detector can still produce one false positive on a noise dataset. The v2 percent-trend 2-percentage-point rule is tightened only for the sales win rate (a chi-squared guard).
- Optional `UZ_POLISH` is off. A rewrite is accepted only when numbers, polarity, quoted names, jargon and length all pass; otherwise the frame text is kept.

## Ask v2 (phase U7)

- Router accuracy is measured on the 60 Uzbek golden questions whose SQL the compiler itself produced; that is a regression lock, not an independent field study. Live-model accuracy on LLM-expected questions is not claimed until `scripts/eval_uzbek.py --model <live>` is run with a key.
- Follow-up inheritance (`shu filial boʻyicha-chi?`) only kicks in when the new question is not already confident, so a fully specified follow-up is routed on its own.
- Russian questions are detected and understood best-effort; answers are still in the UI language (uz or en).

## Product UI (v3)

- The v3 interface is Uzbek and English. The v2 Streamlit app remains English-only.
- Accounts, roles and encrypted storage apply to the FastAPI app. Streamlit still uses an optional shared `APP_PASSWORD` and session memory.
- Dashboard filter presets (Bu oy, …), brush-to-date, PNG export, print PDF, drag-resize layout and axe-core in CI are not all implemented. Cross-filter chips and view-as-table are.
- Playwright in CI is the theme-init check; the first-admin demo walk is the backend smoke test plus UI unit tests. Axe scores are not stored.
- v3 container memory has not been re-measured; `DEPLOY.md` quotes v2 figures as a lower bound.
- Render was not deployed from this machine; `render.yaml` health is `/healthz`.
- `openapi-typescript` is not used; `frontend/src/api/schema.ts` is a generated path list.
- No in-place rotation job for `APP_MASTER_KEY`.
