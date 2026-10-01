"""Data with nothing planted in it: every column is random and independent of the others.

The insight detectors must stay quiet on it (at most one finding), which guards against the
detectors reporting patterns that are only chance.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SEED = 2024
START, END = pd.Timestamp("2024-10-01"), pd.Timestamp("2026-09-30")


def noise_sales(rows: int = 15_000) -> pd.DataFrame:
    """Deals with a constant win rate, a constant funnel and a constant sales cycle."""
    rng = np.random.default_rng(SEED)
    days = (END - START).days + 1
    created = START + pd.to_timedelta(np.sort(rng.integers(0, days, rows)), unit="D")
    cycle = np.maximum(3, rng.lognormal(np.log(34), 0.45, rows)).round()
    close = created + pd.to_timedelta(cycle, unit="D")
    won = rng.random(rows) < 0.30
    status = np.where(won, "won", "lost").astype(object)
    status[np.asarray(close > END)] = "open"
    stages = np.array(["Lead", "Qualified", "Proposal", "Negotiation"], dtype=object)
    reached = np.minimum(rng.geometric(0.3, rows) - 1, 3)  # each step is passed 70% of the time
    stage = stages[reached]
    stage[status == "won"] = "Won"
    return pd.DataFrame(
        {
            "deal_id": [f"D{i:05d}" for i in range(rows)],
            "created_date": created.strftime("%Y-%m-%d"),
            "close_date": np.where(status == "open", "", close.strftime("%Y-%m-%d")),
            "owner_id": rng.choice([f"O{i:02d}" for i in range(1, 9)], rows),
            "region": rng.choice(["North", "South", "East", "West"], rows),
            "source": rng.choice(["website", "referral", "cold_call", "event"], rows),
            "product": rng.choice(["Basic", "Pro", "Enterprise"], rows),
            "stage": stage,
            "status": status,
            "amount": rng.lognormal(15, 0.35, rows).round(0),
        }
    )


def noise_measurements(rows: int = 20_000) -> pd.DataFrame:
    """Measurements where every number and every category is independent of the others."""
    rng = np.random.default_rng(SEED + 1)
    days = (END - START).days + 1
    measured = START + pd.to_timedelta(np.sort(rng.integers(0, days, rows)), unit="D")
    return pd.DataFrame(
        {
            "sample_id": [f"S{i:06d}" for i in range(rows)],
            "measured_at": measured.strftime("%Y-%m-%d"),
            "line": rng.choice(["A", "B", "C"], rows),
            "shift": rng.choice(["day", "night"], rows),
            "temperature": rng.normal(70, 4, rows).round(2),
            "pressure": np.where(
                rng.random(rows) < 0.05, np.nan, rng.normal(100, 8, rows).round(2)
            ),
            "thickness_mm": rng.normal(5.0, 0.15, rows).round(3),
            "humidity": rng.normal(45, 6, rows).round(1),
        }
    )
