"""Evaluate the Uzbek Ask v2 golden set (router path, offline).

Usage:
    python scripts/eval_uzbek.py
    python scripts/eval_uzbek.py --model offline

Reports router coverage and accuracy on router-expected questions. A live model is only
used for questions marked ``router_expected: false``. Writes ``reports/eval_uzbek_<date>.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

from core.config import load_settings  # noqa: E402
from core.ingest.demo import DEMO_DIR, build_demo_model, load_demo  # noqa: E402
from core.llm.audit import AuditLog  # noqa: E402
from core.llm.gateway import LLMGateway  # noqa: E402
from core.nlu.ask import AskAgent  # noqa: E402
from core.query.evaluation import same_result  # noqa: E402
from core.query.executor import execute_query  # noqa: E402

REPORT_DIR = Path("reports")


def load_questions() -> list[dict[str, Any]]:
    data = yaml.safe_load((DEMO_DIR / "golden_questions_uz.yaml").read_text(encoding="utf-8"))
    return data["questions"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="offline")
    args = parser.parse_args()
    settings = load_settings()
    if args.model == "offline":
        settings = settings.model_copy(update={"llm_mode": "offline"})
    questions = load_questions()
    audit = AuditLog()
    gateway = LLMGateway(settings, audit)
    outcomes = []
    by_dataset: dict[str, list[dict]] = {}
    for item in questions:
        by_dataset.setdefault(item["dataset"], []).append(item)
    for name, items in by_dataset.items():
        workspace = load_demo(name, settings)
        model = build_demo_model(workspace)
        agent = AskAgent(
            workspace.con, model, gateway, settings, pack=name, demo_key=name, locale="uz"
        )
        for item in items:
            expected = execute_query(workspace.con, item["expected_sql"], settings)
            answer = agent.ask(item["question"], locale="uz")
            passed = (
                answer.status == "ok"
                and answer.table is not None
                and same_result(answer.table, expected)
            )
            outcomes.append(
                {
                    "id": item["id"],
                    "dataset": name,
                    "router_expected": item.get("router_expected", True),
                    "passed": passed,
                    "status": answer.status,
                    "verified": answer.verified,
                }
            )
            mark = "PASS" if passed else "FAIL"
            print(f"{item['id']:<16}{mark:<6}{answer.status}")
    router = [o for o in outcomes if o["router_expected"]]
    passed = sum(o["passed"] for o in router)
    summary = {
        "date": date.today().isoformat(),
        "model": args.model,
        "router_expected": len(router),
        "router_passed": passed,
        "router_accuracy": round(passed / len(router), 4) if router else 0,
        "total": len(outcomes),
        "passed": sum(o["passed"] for o in outcomes),
        "outcomes": outcomes,
    }
    REPORT_DIR.mkdir(exist_ok=True)
    path = REPORT_DIR / f"eval_uzbek_{date.today().isoformat()}.json"
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nrouter: {passed}/{len(router)} = {summary['router_accuracy']:.1%}")
    print(f"wrote {path}")
    return 0 if passed == len(router) else 1


if __name__ == "__main__":
    sys.exit(main())
