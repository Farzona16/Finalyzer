"""Small generated tables with English, Uzbek and Russian column names for the autopilot tests.

Every builder returns a pandas frame of strings and numbers like a user's file would hold. The
frames go through the real ingestion code (``model_from_frame``), so the classifier sees what it
sees in production. The names were chosen the way a clerk in each language would write them,
not copied from the pack hints.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.ingest.flatten import build_dataset, pick_fact_table, propose, workspace_from_raw
from tests.helpers import DemoDataset

ROWS = 400


def rng() -> np.random.Generator:
    return np.random.default_rng(42)


def dates(generator: np.random.Generator, n: int = ROWS) -> list[str]:
    start = pd.Timestamp("2025-01-01")
    return [
        (start + pd.Timedelta(days=int(d))).strftime("%Y-%m-%d")
        for d in generator.integers(0, 360, n)
    ]


def pick(
    generator: np.random.Generator, values: list[str], weights: list[float] | None = None
) -> list[str]:
    return list(generator.choice(values, ROWS, p=weights))


def retail(columns: dict[str, str], values: dict[str, list[str]]) -> pd.DataFrame:
    """Retail-like sales rows. ``columns`` maps the logical names to the file's names."""
    g = rng()
    amount = np.round(g.gamma(3.0, 40.0, ROWS), 2)
    data = {
        columns["id"]: [f"{i + 1000}" for i in range(ROWS)],
        columns["date"]: dates(g),
        columns["product"]: pick(g, values["product"]),
        columns["qty"]: g.integers(1, 6, ROWS),
        columns["amount"]: amount,
        columns["cost"]: np.round(amount * g.uniform(0.5, 0.8, ROWS), 2),
        columns["customer"]: g.integers(1, 80, ROWS),
        columns["status"]: pick(g, values["status"], [0.85, 0.1, 0.05]),
    }
    return pd.DataFrame(data)


def retail_en() -> pd.DataFrame:
    return retail(
        {
            "id": "sale_id",
            "date": "sale_date",
            "product": "product_name",
            "qty": "quantity",
            "amount": "amount",
            "cost": "cost_amount",
            "customer": "customer_id",
            "status": "payment_status",
        },
        {
            "product": ["Tea", "Coffee", "Sugar", "Flour", "Rice"],
            "status": ["paid", "refunded", "pending"],
        },
    )


def retail_uz() -> pd.DataFrame:
    return retail(
        {
            "id": "chek_raqami",
            "date": "sana",
            "product": "mahsulot",
            "qty": "miqdor",
            "amount": "summa",
            "cost": "tannarx",
            "customer": "mijoz_id",
            "status": "tolov_holati",
        },
        {
            "product": ["Choy", "Qahva", "Shakar", "Un", "Guruch"],
            "status": ["tolangan", "qaytarilgan", "kutilmoqda"],
        },
    )


def retail_ru() -> pd.DataFrame:
    return retail(
        {
            "id": "номер_чека",
            "date": "дата",
            "product": "товар",
            "qty": "количество",
            "amount": "сумма",
            "cost": "себестоимость",
            "customer": "клиент_id",
            "status": "статус_оплаты",
        },
        {
            "product": ["Чай", "Кофе", "Сахар", "Мука", "Рис"],
            "status": ["оплачено", "возврат", "ожидание"],
        },
    )


def sales(columns: dict[str, str], values: dict[str, list[str]]) -> pd.DataFrame:
    """CRM deals: a stage, an owner, a source, a won/lost/open status."""
    g = rng()
    status = pick(g, values["status"], [0.3, 0.45, 0.25])
    created = dates(g)
    closed = [
        (pd.Timestamp(c) + pd.Timedelta(days=int(d))).strftime("%Y-%m-%d")
        for c, d in zip(created, g.integers(5, 90, ROWS), strict=True)
    ]
    data = {
        columns["id"]: [f"D{i:04d}" for i in range(ROWS)],
        columns["date"]: created,
        columns["owner"]: pick(g, values["owner"]),
        columns["source"]: pick(g, values["source"]),
        columns["stage"]: pick(g, values["stage"]),
        columns["status"]: status,
        columns["amount"]: np.round(g.gamma(2.0, 5000.0, ROWS), 0),
    }
    if "close" in columns:
        data[columns["close"]] = closed
    return pd.DataFrame(data)


def sales_en() -> pd.DataFrame:
    return sales(
        {
            "id": "deal_id",
            "date": "created_at",
            "owner": "sales_rep",
            "source": "lead_source",
            "stage": "pipeline_stage",
            "status": "deal_status",
            "amount": "deal_value",
            "close": "closed_at",
        },
        {
            "owner": ["Anna", "Bek", "Carl"],
            "source": ["web", "referral", "event"],
            "stage": ["Lead", "Qualified", "Proposal", "Negotiation", "Won"],
            "status": ["won", "lost", "open"],
        },
    )


def sales_uz() -> pd.DataFrame:
    return sales(
        {
            "id": "bitim_id",
            "date": "sana",
            "owner": "menejer",
            "source": "manba",
            "stage": "bosqich",
            "status": "holat",
            "amount": "summa",
        },
        {
            "owner": ["Aziz", "Dilnoza", "Sardor"],
            "source": ["sayt", "tavsiya", "tadbir"],
            "stage": ["Soʻrov", "Saralangan", "Taklif", "Muzokara", "Yutildi"],
            "status": ["yutildi", "yutqazildi", "ochiq"],
        },
    )


def sales_ru() -> pd.DataFrame:
    return sales(
        {
            "id": "сделка",
            "date": "дата",
            "owner": "менеджер",
            "source": "источник",
            "stage": "этап",
            "status": "статус",
            "amount": "сумма",
        },
        {
            "owner": ["Иван", "Ольга", "Петр"],
            "source": ["сайт", "рекомендация", "выставка"],
            "stage": ["Лид", "Квалификация", "Предложение", "Переговоры", "Выиграно"],
            "status": ["выиграно", "проиграно", "открыта"],
        },
    )


def bank(columns: dict[str, str], values: dict[str, list[str]]) -> pd.DataFrame:
    g = rng()
    amount = np.round(g.lognormal(4.5, 1.0, ROWS), 2)
    return pd.DataFrame(
        {
            columns["id"]: [f"T{i:05d}" for i in range(ROWS)],
            columns["date"]: dates(g),
            columns["customer"]: g.integers(1, 90, ROWS),
            columns["channel"]: pick(g, values["channel"]),
            columns["amount"]: amount,
            columns["status"]: pick(g, values["status"], [0.93, 0.05, 0.02]),
            columns["fee"]: np.round(amount * 0.01, 2),
            columns["branch"]: pick(g, values["branch"]),
        }
    )


def bank_en() -> pd.DataFrame:
    return bank(
        {
            "id": "txn_id",
            "date": "txn_date",
            "customer": "customer_id",
            "channel": "channel",
            "amount": "amount",
            "status": "status",
            "fee": "fee",
            "branch": "branch",
        },
        {
            "channel": ["card", "atm", "mobile"],
            "status": ["completed", "failed", "reversed"],
            "branch": ["Central", "North", "South"],
        },
    )


def bank_uz() -> pd.DataFrame:
    return bank(
        {
            "id": "tranzaksiya_id",
            "date": "sana",
            "customer": "mijoz_id",
            "channel": "kanal",
            "amount": "summa",
            "status": "holat",
            "fee": "komissiya",
            "branch": "filial",
        },
        {
            "channel": ["karta", "bankomat", "mobil"],
            "status": ["yakunlangan", "muvaffaqiyatsiz", "bekor"],
            "branch": ["Markaziy", "Shimol", "Janub"],
        },
    )


def project_en() -> pd.DataFrame:
    g = rng()
    n = 120
    budget = np.round(g.uniform(10_000, 90_000, n), 0)
    planned = pd.Timestamp("2025-06-01") + pd.to_timedelta(g.integers(0, 200, n), unit="D")
    delay = pd.to_timedelta(g.integers(-10, 25, n), unit="D")
    total = g.integers(10, 60, n)
    return pd.DataFrame(
        {
            "project_id": [f"P{i:03d}" for i in range(n)],
            "status": list(
                g.choice(["completed", "in_progress", "on_hold"], n, p=[0.5, 0.35, 0.15])
            ),
            "budget": budget,
            "actual_cost": np.round(budget * g.uniform(0.8, 1.3, n), 0),
            "manager": list(g.choice(["Kim", "Lee", "Park", "Choi"], n)),
            "planned_end": planned.strftime("%Y-%m-%d"),
            "actual_end": (planned + delay).strftime("%Y-%m-%d"),
            "tasks_total": total,
            "tasks_done": (total * g.uniform(0.3, 1.0, n)).astype(int),
        }
    )


def expenses_uz() -> pd.DataFrame:
    g = rng()
    return pd.DataFrame(
        {
            "xarajat_turi": pick(g, ["ijara", "maosh", "reklama", "transport", "kommunal"]),
            "sana": dates(g),
            "summa": np.round(g.gamma(2.0, 150_000.0, ROWS), 0),
            "taminotchi": pick(g, ["Alfa", "Beta", "Gamma", "Delta"]),
            "bolim": pick(g, ["savdo", "ishlab chiqarish", "ofis"]),
        }
    )


def statistics_ru() -> pd.DataFrame:
    g = rng()
    return pd.DataFrame(
        {
            "номер_образца": [f"S{i:04d}" for i in range(ROWS)],
            "партия": pick(g, ["A1", "A2", "B1", "B2"]),
            "температура": np.round(g.normal(70, 4, ROWS), 2),
            "давление": np.round(g.normal(100, 8, ROWS), 2),
            "толщина": np.round(g.normal(5, 0.2, ROWS), 3),
            "брак": g.poisson(3, ROWS),
        }
    )


def generic_contacts() -> pd.DataFrame:
    g = rng()
    return pd.DataFrame(
        {
            "city": pick(g, ["Tashkent", "Samarkand", "Bukhara", "Khiva", "Nukus", "Fergana"]),
            "country": pick(g, ["UZ", "KZ", "KG"]),
            "kind": pick(g, ["cafe", "shop", "school", "clinic"]),
            "rating": g.integers(1, 6, ROWS),
            "note": pick(g, ["good", "average", "closed", "new", "renovated"]),
        }
    )


def hr_attrition() -> pd.DataFrame:
    """The unseen domain: no pack knows about employees."""
    g = rng()
    return pd.DataFrame(
        {
            "employee_id": list(range(1, ROWS + 1)),
            "age": g.integers(20, 60, ROWS),
            "department": pick(g, ["Sales", "R&D", "HR", "Finance"], [0.4, 0.3, 0.1, 0.2]),
            "monthly_income": np.round(g.gamma(4.0, 1500.0, ROWS), 0),
            "years_at_company": g.integers(0, 25, ROWS),
            "job_satisfaction": g.integers(1, 5, ROWS),
            "overtime": pick(g, ["Yes", "No"], [0.3, 0.7]),
            "attrition": pick(g, ["Yes", "No"], [0.16, 0.84]),
        }
    )


@dataclass(frozen=True)
class Fixture:
    name: str
    expected: str
    build: Callable[[], pd.DataFrame]


CLASSIFIER_FIXTURES = (
    Fixture("retail_en", "retail", retail_en),
    Fixture("retail_uz", "retail", retail_uz),
    Fixture("retail_ru", "retail", retail_ru),
    Fixture("sales_en", "sales", sales_en),
    Fixture("sales_uz", "sales", sales_uz),
    Fixture("sales_ru", "sales", sales_ru),
    Fixture("bank_en", "bank", bank_en),
    Fixture("bank_uz", "bank", bank_uz),
    Fixture("project_en", "project", project_en),
    Fixture("expenses_uz", "expenses", expenses_uz),
    Fixture("statistics_ru", "statistics", statistics_ru),
    Fixture("generic_contacts", "generic", generic_contacts),
)


def model_from_frame(frame: pd.DataFrame, name: str = "records") -> DemoDataset:
    """Run a frame through the real cleaning, profiling and flattening code."""
    workspace = workspace_from_raw({name: frame.astype(str)})
    fact = pick_fact_table(workspace)
    model = build_dataset(workspace, fact, propose(workspace, fact))
    return DemoDataset(name, workspace, model)
