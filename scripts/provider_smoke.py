"""Live smoke test of one provider: list models, then one token-capped call (spec V1).

Used by ``scripts/claude_smoke.py`` and ``scripts/grok_smoke.py``. Without a key it prints
``not run (no key)`` and exits 0, so CI without secrets stays green. The call goes through the
Egress Gate with a synthetic prompt (no data) and at most 64 output tokens. The key is never
printed. A report is written to ``reports/smoke_<provider>_<date>.json``.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel  # noqa: E402

from core.config import load_settings  # noqa: E402
from core.llm.audit import AuditLog  # noqa: E402
from core.llm.gateway import LLMGateway  # noqa: E402
from core.llm.registry import LLMRuntime, build_providers, verify_models  # noqa: E402

REPORT_DIR = Path("reports")
KEY_FIELD = {"anthropic": "anthropic_api_key", "grok": "xai_api_key"}


class Pong(BaseModel):
    reply: str


def run(provider: str) -> dict[str, Any]:
    base = load_settings()
    report: dict[str, Any] = {"provider": provider, "date": date.today().isoformat()}
    if not getattr(base, KEY_FIELD[provider]).strip():
        return {**report, "status": "not run (no key)"}
    settings = base.model_copy(update={
        "llm_mode": "live", "llm_provider_chain": f"{provider},offline",
        "cache_identical_prompts": False,
    })
    if not settings.provider_configured(provider):
        return {**report, "status": "not run (no model configured)"}
    runtime = LLMRuntime()
    providers = build_providers(settings, runtime)
    check = verify_models(providers, runtime)[provider]
    report["models"] = dict(providers[0].models)
    report["model_check"] = {k: v for k, v in check.items() if k != "available"}
    report["available_models"] = check.get("available", [])[:50]
    if runtime.is_skipped(provider):
        return {**report, "status": "failed", "state": runtime.status(provider).state}

    gateway = LLMGateway(settings, AuditLog(), runtime=runtime, data_origin="synthetic")
    messages = [
        {"role": "system", "content": "You are a connectivity check."},
        {"role": "user", "content": "Answer with reply set to the word pong."},
    ]
    started = time.perf_counter()
    try:
        value, response = gateway.complete_json_meta("smoke", messages, Pong)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return {**report, "status": "failed", "error": type(exc).__name__,
                "message": getattr(exc, "message", "")}
    report.update(
        status="ok",
        reply=value.reply[:40],
        structured=response.tool_args is not None,
        model=response.model,
        tokens_in=response.tokens_in,
        tokens_out=response.tokens_out,
        latency_s=round(time.perf_counter() - started, 2),
    )
    return report


def main(provider: str) -> int:
    report = run(provider)
    print(json.dumps(report, indent=2))
    if report["status"].startswith("not run"):
        return 0
    REPORT_DIR.mkdir(exist_ok=True)
    path = REPORT_DIR / f"smoke_{provider}_{report['date']}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"report: {path}")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "anthropic"))
