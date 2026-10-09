"""Run the 40 golden questions through the real NL to SQL pipeline and report accuracy.

Usage:
    python scripts/eval_golden.py --model offline
    python scripts/eval_golden.py --provider anthropic --model <model id> --candidates 2

``--model offline`` uses the offline provider (the stored SQL of each question), which only
proves that the harness works. Otherwise ``--provider`` (anthropic, grok or ollama) is the only
link of the chain and ``--model`` overrides its main model; keys and endpoints come from the
environment or .env (ANTHROPIC_API_KEY, XAI_API_KEY, LLM_API_BASE). A report is written to
``reports/eval_<model>_<date>.json``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import Settings, load_settings  # noqa: E402
from core.errors import UserFacingError  # noqa: E402
from core.ingest.demo import DEMO_NAMES, build_demo_model, load_demo  # noqa: E402
from core.llm.audit import AuditLog  # noqa: E402
from core.llm.gateway import LLMGateway  # noqa: E402
from core.llm.providers import load_golden_questions  # noqa: E402
from core.query.evaluation import (  # noqa: E402
    QuestionOutcome,
    evaluate_dataset,
    outcome_rows,
    summarize,
)

REPORT_DIR = Path("reports")


MODEL_FIELD = {"anthropic": "claude_model_main", "grok": "grok_model_main", "ollama": "llm_model"}


def build_settings(model: str, candidates: int, provider: str = "anthropic") -> Settings:
    """Settings for the run: offline for ``offline``, otherwise one provider with ``model``."""
    base = load_settings()
    if model == "offline":
        return base.model_copy(update={"llm_mode": "offline", "sql_candidates": candidates})
    update: dict[str, Any] = {
        "llm_mode": "local" if provider == "ollama" else "live",
        "llm_provider_chain": f"{provider},offline",
        "sql_candidates": candidates,
        "ask_candidates": None,
        "session_token_budget": max(base.session_token_budget, 2_000_000),
        "daily_token_budget": max(base.daily_token_budget, 2_000_000),
    }
    if model:
        update[MODEL_FIELD[provider]] = model
    settings = base.model_copy(update=update)
    if not settings.provider_configured(provider):
        raise UserFacingError(
            f"The {provider} provider is not configured (key and model). Use --model offline, "
            "or set them in the environment."
        )
    return settings


def safe_name(model: str) -> str:
    """A file-name-safe version of a model string."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-") or "model"


def print_table(outcomes: list[QuestionOutcome]) -> None:
    """One line per question."""
    print(f"{'id':<12}{'result':<7}{'verified':<10}{'latency':>9}{'tokens':>8}  note")
    for o in outcomes:
        verdict = "PASS" if o.passed else "FAIL"
        print(
            f"{o.id:<12}{verdict:<7}{'yes' if o.verified else 'no':<10}"
            f"{o.latency_s:>8.2f}s{o.tokens:>8}  {o.note}"
        )


def print_summary(summary: dict[str, Any]) -> None:
    """The totals under the table."""
    print(f"\naccuracy: {summary['passed']}/{summary['total']} = {summary['accuracy']:.1%}")
    for label in ("verified", "unverified"):
        part = summary[label]
        print(f"{label}: {part['count']} answers, {part['passed']} correct")
    print(f"mean latency: {summary['mean_latency_s']:.2f} s")
    print(f"tokens: {summary['tokens_in']:,} in, {summary['tokens_out']:,} out "
          f"({summary['model_calls']} model calls)")


def run(
    model: str, candidates: int, datasets: list[str], provider: str = "anthropic"
) -> tuple[list[QuestionOutcome], dict]:
    """Evaluate the chosen demo datasets and return outcomes and summary."""
    settings = build_settings(model, candidates, provider)
    audit = AuditLog()
    gateway = LLMGateway(settings, audit)
    questions = load_golden_questions()
    outcomes: list[QuestionOutcome] = []
    for name in datasets:
        workspace = load_demo(name, settings)
        data_model = build_demo_model(workspace)
        outcomes += evaluate_dataset(workspace.con, data_model, name, questions, gateway, settings)
        workspace.con.close()
    return outcomes, summarize(outcomes, audit)


def write_report(
    model: str, candidates: int, outcomes: list[QuestionOutcome], summary: dict
) -> Path:
    """Store the report as JSON."""
    REPORT_DIR.mkdir(exist_ok=True)
    path = REPORT_DIR / f"eval_{safe_name(model)}_{date.today().isoformat()}.json"
    report = {
        "model": model,
        "date": date.today().isoformat(),
        "candidates": candidates,
        "summary": summary,
        "questions": outcome_rows(outcomes),
    }
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def main() -> int:
    """Parse arguments, run the evaluation, print and store the report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="model id of the provider, or 'offline'")
    parser.add_argument("--provider", choices=sorted(MODEL_FIELD), default="anthropic")
    parser.add_argument("--candidates", type=int, default=3)
    parser.add_argument("--dataset", choices=[*DEMO_NAMES, "all"], default="all")
    args = parser.parse_args()
    datasets = list(DEMO_NAMES) if args.dataset == "all" else [args.dataset]
    try:
        outcomes, summary = run(args.model, args.candidates, datasets, args.provider)
    except UserFacingError as exc:
        print(exc.message, file=sys.stderr)
        return 2
    print_table(outcomes)
    print_summary(summary)
    print(f"report: {write_report(args.model, args.candidates, outcomes, summary)}")
    return 0 if summary["passed"] == summary["total"] or args.model != "offline" else 1


if __name__ == "__main__":
    raise SystemExit(main())
