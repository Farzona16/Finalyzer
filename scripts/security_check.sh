#!/usr/bin/env bash
# S18: dependency and static security checks. Results go to reports/security_check.txt.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/reports/security_check.txt}"
mkdir -p "$(dirname "$OUT")"
{
  echo "Finalyzer security check $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo
  echo "== pip-audit (root lock) =="
  (cd "$ROOT" && python -m pip_audit -r requirements.lock.txt) || true
  echo
  echo "== pip-audit (backend lock if present) =="
  if [[ -f "$ROOT/backend/requirements.lock.txt" ]]; then
    (cd "$ROOT/backend" && python -m pip_audit -r requirements.lock.txt) || true
  fi
  echo
  echo "== npm audit (frontend, production) =="
  (cd "$ROOT/frontend" && npm audit --omit=dev) || true
  echo
  echo "== bandit (backend/app, core) =="
  (cd "$ROOT" && python -m bandit -q -r backend/app core -x '**/tests/**') || true
  echo
  echo "== eslint-plugin-security (frontend) =="
  (cd "$ROOT/frontend" && npm run lint) || true
} | tee "$OUT"
echo "wrote $OUT"
