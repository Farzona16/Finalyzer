"""Build data/demo/golden_questions_uz.yaml from the router+compiler (run once, then keep the file)."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.nlu.compiler import compile_sql
from core.nlu.entities import Catalog
from core.nlu.router import route_question
from core.metrics.registry import available_metrics, primary_metric
from tests.helpers import build_demo

QUESTIONS: list[tuple[str, str, str | None]] = [
    # dataset, uz, english parallel or None
    ("retail", "Jami daromad qancha?", "What is the total revenue?"),
    ("retail", "Daromad kategoriya boʻyicha qancha?", "What is the revenue by category?"),
    (
        "retail",
        "Qaysi 5 ta doʻkon daromadi eng yuqori?",
        "Which 5 stores have the highest revenue?",
    ),
    ("retail", "Daromad oylar boʻyicha qanday oʻzgardi?", "Show the monthly revenue over time."),
    (
        "retail",
        "2026-yil sentabrda daromad qancha boʻldi?",
        "What was the revenue in September 2026?",
    ),
    ("retail", "Oxirgi oyda daromad qancha?", "What was the revenue last month?"),
    ("retail", "Oʻrtacha buyurtma summasi kanal boʻyicha qancha?", None),
    ("retail", "Yalpi foyda ulushi kategoriya boʻyicha qancha?", None),
    (
        "retail",
        "2026 yilda nechta turli mijoz xarid qildi?",
        "How many distinct customers bought in 2026?",
    ),
    (
        "retail",
        "Daromad oʻtgan yilga nisbatan qanday oʻzgardi?",
        "What is the year-over-year revenue growth by month?",
    ),
    ("retail", "Qaysi doʻkon eng yaxshi natija koʻrsatdi?", "Which store performed best?"),
    ("retail", "Jami sotilgan dona qancha?", None),
    ("retail", "Store 03 da daromad qancha?", None),
    (
        "retail",
        "Daromad oyma-oy qanday oʻzgardi?",
        "What is the month-over-month change in revenue?",
    ),
    ("retail", "Jami buyurtmalar qancha?", "How many orders were placed in total?"),
    ("bank", "Jami tranzaksiyalar hajmi qancha?", "What is the total transaction volume?"),
    ("bank", "Tranzaksiyalar soni qancha?", "How many transactions are there?"),
    ("bank", "Hajm filial boʻyicha qancha?", "What is volume by branch?"),
    ("bank", "Qaysi 5 ta filial hajmi eng yuqori?", "Which 5 branches have the highest volume?"),
    ("bank", "Hajm oylar boʻyicha qanday oʻzgardi?", "Show monthly transaction volume."),
    ("bank", "Oxirgi oyda hajm qancha?", "What was the volume last month?"),
    ("bank", "Muvaffaqiyatsiz tranzaksiyalar ulushi qancha?", "What share of transactions failed?"),
    ("bank", "Hajm kanal boʻyicha qancha?", None),
    ("bank", "Oʻrtacha tranzaksiya summasi qancha?", None),
    ("bank", "2026-yil sentabrda hajm qancha?", None),
    ("bank", "Qaysi filial eng yaxshi natija koʻrsatdi?", "Which branch performed best?"),
    ("bank", "Muvaffaqiyatsiz ulushi kanal boʻyicha qancha?", None),
    ("bank", "Hajm oʻtgan yilga nisbatan qanday oʻzgardi?", None),
    ("bank", "Branch 07 da hajm qancha?", None),
    ("bank", "Jami tranzaksiyalar soni qancha?", None),
    ("sales", "Yutilgan bitimlar daromadi qancha?", "What is the won revenue?"),
    ("sales", "Yutish ulushi qancha?", "What is the win rate?"),
    ("sales", "Yutilgan bitimlar egasi boʻyicha qancha?", None),
    (
        "sales",
        "Yutish ulushi oylar boʻyicha qanday oʻzgardi?",
        "How did the win rate change by month?",
    ),
    ("sales", "Ochiq bitimlar summasi qancha?", "What is the pipeline value?"),
    ("sales", "Nechta bitim toʻxtab qolgan?", "How many deals are stalled?"),
    ("sales", "Bosqichlar boʻyicha nechta bitim?", None),
    ("sales", "Oxirgi oyda yutilgan bitimlar daromadi qancha?", None),
    ("sales", "Yutilgan bitimlar daromadi manba boʻyicha qancha?", None),
    ("sales", "Yutilgan bitimlar daromadi mahsulot boʻyicha qancha?", None),
    ("sales", "Oʻrtacha bitim summasi qancha?", None),
    ("sales", "Yutqazilgan bitimlar qancha?", None),
    ("sales", "Nega yutilgan bitimlar daromadi tushdi?", "Why did won revenue drop?"),
    ("sales", "Qaysi 3 ta egasi eng koʻp yutgan?", None),
    ("sales", "Yutish ulushi manba boʻyicha qancha?", None),
    ("statistics", "Namunalar soni qancha?", "How many samples are there?"),
    ("statistics", "Tekshiruvdan oʻtganlar ulushi qancha?", "What is the pass rate?"),
    ("statistics", "Harorat qanday tarqalgan?", "How is temperature distributed?"),
    ("statistics", "Harorat va bosim bogʻliqmi?", "Are temperature and pressure correlated?"),
    ("statistics", "Namunalar soni liniya boʻyicha qancha?", None),
    ("statistics", "Namunalar soni smena boʻyicha qancha?", None),
    ("statistics", "defects ustunida yetishmayotgan qiymatlar qancha?", None),
    ("statistics", "Maksimal harorat qancha?", "What is the maximum temperature?"),
    ("statistics", "Namunalar soni oylar boʻyicha qanday oʻzgardi?", None),
    ("statistics", "Tekshiruvdan oʻtganlar ulushi liniya boʻyicha qancha?", None),
    ("statistics", "Qalinlikning oʻrtadagi qiymati qancha?", None),
    ("statistics", "Minimal harorat qancha?", None),
    ("statistics", "Oʻrtacha harorat qancha?", None),
    ("statistics", "Maksimal bosim qancha?", None),
    ("statistics", "Namunalar soni oxirgi oyda qancha?", None),
]


def latest(demo):
    roles = demo.model.roles
    if "date" not in roles:
        return None
    value = demo.con.execute(f'SELECT MAX("{roles["date"]}") FROM data').fetchone()[0]
    return value.date() if isinstance(value, datetime) else value


def main() -> None:
    demos = {name: build_demo(name) for name in ("retail", "bank", "sales", "statistics")}
    lines = [
        "# Uzbek golden questions for Ask v2 (15 per demo dataset).",
        "# expected_sql is produced by the deterministic compiler; result-set equality is the test.",
        "questions:",
    ]
    counts: dict[str, int] = {}
    failed = []
    for dataset, uz, english in QUESTIONS:
        demo = demos[dataset]
        metrics = available_metrics(demo.model, dataset)
        cat = Catalog.of(demo.model, metrics, primary_metric(demo.model, dataset))
        route = route_question(uz, cat, latest(demo))
        sql = compile_sql(route, demo.model.roles) if route.confident else None
        counts[dataset] = counts.get(dataset, 0) + 1
        qid = f"{dataset}_{counts[dataset]:02d}"
        ok = bool(route.confident and sql)
        if not ok:
            failed.append((qid, uz, route.intent, route.clarify, route.confident))
        sql_block = (sql or "SELECT 1").replace("\n", " ")
        lines.append(f"  - id: {qid}")
        lines.append(f"    dataset: {dataset}")
        lines.append(
            f"    question: {uz!r}".replace("'", '"') if False else f'    question: "{uz}"'
        )
        lines.append("    expected_sql: >-")
        lines.append(f"      {sql_block}")
        lines.append(f"    router_expected: {'true' if ok else 'false'}")
        lines.append(f"    intent: {route.intent}")
        if english:
            lines.append(f'    english: "{english}"')
    out = Path("data/demo/golden_questions_uz.yaml")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(QUESTIONS)} questions, {len(failed)} not confident)")
    for row in failed:
        print("FAIL", row)


if __name__ == "__main__":
    main()
