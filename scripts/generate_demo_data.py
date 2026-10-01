"""Generate the deterministic synthetic demo datasets (retail and bank).

Running this script twice produces byte-identical files. Each dataset contains the eight
planted patterns described in Appendix A of the specification; comments next to the code
mark which pattern (R1..R8, B1..B8) a piece of code creates.

Usage: python scripts/generate_demo_data.py [--out data/demo]
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
START = date(2024, 10, 1)
END = date(2026, 9, 30)
LAST_MONTH = 23  # month number of 2026-09 (2024-10 is month 0)

FIRST_NAMES = [
    "Alex", "Maria", "John", "Anna", "David", "Elena", "Michael", "Sara", "Daniel", "Laura",
    "Peter", "Olga", "Thomas", "Nina", "Robert", "Irina", "Kevin", "Julia", "Steven", "Emma",
    "Adam", "Sofia", "Victor", "Lucy", "Paul", "Helen", "Marcus", "Diana", "Oscar", "Nora",
    "Felix", "Clara", "Simon", "Alice", "Henry", "Vera", "Leo", "Rita", "Max", "Eva",
]
LAST_NAMES = [
    "Smith", "Brown", "Miller", "Davis", "Wilson", "Moore", "Taylor", "Clark", "Hall", "Young",
    "King", "Wright", "Scott", "Green", "Baker", "Adams", "Nelson", "Hill", "Campbell", "Mitchell",
    "Roberts", "Carter", "Phillips", "Evans", "Turner", "Parker", "Collins", "Edwards", "Stewart",
    "Morris", "Murphy", "Cook", "Rogers", "Morgan", "Cooper", "Peterson", "Reed", "Bailey",
    "Bell", "Kelly",
]
CITIES = [
    "Springfield", "Riverton", "Lakeside", "Fairview", "Georgetown", "Madison", "Clinton", "Salem",
]


# ---------------------------------------------------------------- shared helpers


def build_days() -> pd.DatetimeIndex:
    """Return every day of the demo period."""
    return pd.date_range(START, END, freq="D")


def month_numbers(days: pd.DatetimeIndex) -> np.ndarray:
    """Return 0 for 2024-10 up to 23 for 2026-09."""
    return ((days.year - START.year) * 12 + days.month - START.month).to_numpy()


def ramp(days: pd.DatetimeIndex, start: str, end: str, peak: float) -> np.ndarray:
    """Return 1.0 until ``start``, a straight line up to ``peak`` at ``end``, then ``peak``."""
    t0, t1 = pd.Timestamp(start), pd.Timestamp(end)
    fraction = np.asarray((days - t0) / (t1 - t0), dtype=float)
    return 1.0 + (peak - 1.0) * np.clip(fraction, 0.0, 1.0)


def expected_daily_rows(
    days: pd.DatetimeIndex,
    total_rows: int,
    weekday_factors: list[float],
    yearly_growth: float,
    dim_multiplier: np.ndarray,
) -> np.ndarray:
    """Return the expected number of rows per day, scaled to about ``total_rows``."""
    weekday = np.asarray(weekday_factors)[days.dayofweek.to_numpy()]
    years = np.arange(len(days)) / 365.0
    raw = (1.0 + yearly_growth) ** years * weekday * dim_multiplier
    return raw * (total_rows / raw.sum())


def apply_day_factors(
    days: pd.DatetimeIndex, expected: np.ndarray, factors: dict[str, float]
) -> np.ndarray:
    """Multiply the expected rows of single days (used for the anomaly patterns)."""
    result = expected.copy()
    for text, factor in factors.items():
        result[days.get_loc(pd.Timestamp(text))] *= factor
    return result


def sample_groups(
    rng: np.random.Generator, day_index: np.ndarray, weights: np.ndarray
) -> np.ndarray:
    """Draw a group number per row from per-day weights (days x groups)."""
    cumulative = np.cumsum(weights, axis=1)
    cumulative = cumulative / cumulative[:, -1:]
    draws = rng.random(len(day_index))
    return (draws[:, None] > cumulative[day_index]).sum(axis=1)


def churn_schedule(rng: np.random.Generator, baseline: float, last: float) -> list[float]:
    """Monthly churn probabilities: a noisy baseline and a jump in the last month."""
    values = np.clip(baseline + rng.normal(0.0, 0.01, LAST_MONTH + 1), 0.0, 1.0)
    values[LAST_MONTH] = last
    return [float(v) for v in values]


def next_active_set(
    rng: np.random.Generator,
    everyone: np.ndarray,
    previous: np.ndarray,
    churn: float,
    target: int,
) -> np.ndarray:
    """Choose who is active this month: survivors of last month plus fresh customers.

    Customers who were active last month and lost are never redrawn as "fresh", so the
    measured churn rate equals ``churn`` up to sampling noise.
    """
    if len(previous) == 0:
        return rng.choice(everyone, size=target, replace=False)
    kept = previous[rng.random(len(previous)) >= churn]
    pool = np.setdiff1d(everyone, previous)
    needed = min(max(target - len(kept), 0), len(pool))
    fresh = rng.choice(pool, size=needed, replace=False)
    return np.concatenate([kept, fresh])


def spread_over_rows(rng: np.random.Generator, active: np.ndarray, n_rows: int) -> np.ndarray:
    """Give every active customer at least one row; fill the remaining rows at random."""
    active = rng.permutation(active)[:n_rows]
    extra = rng.choice(active, size=n_rows - len(active), replace=True)
    return rng.permutation(np.concatenate([active, extra]))


def simulate_customers(
    rng: np.random.Generator,
    month_of_row: np.ndarray,
    n_customers: int,
    churn_by_month: list[float],
    active_share: float,
) -> np.ndarray:
    """Return a customer id for every row so that monthly churn follows the schedule."""
    assigned = np.zeros(len(month_of_row), dtype=np.int64)
    everyone = np.arange(1, n_customers + 1)
    previous = np.array([], dtype=np.int64)
    for month, churn in enumerate(churn_by_month):
        rows = np.flatnonzero(month_of_row == month)
        target = min(int(active_share * len(rows)), int(0.85 * n_customers))
        active = next_active_set(rng, everyone, previous, churn, target)
        assigned[rows] = spread_over_rows(rng, active, len(rows))
        previous = np.unique(assigned[rows])
    return assigned


def person_columns(rng: np.random.Generator, count: int) -> pd.DataFrame:
    """Create fake names for ``count`` customers (these columns are PII on purpose)."""
    first = rng.choice(FIRST_NAMES, size=count)
    last = rng.choice(LAST_NAMES, size=count)
    ids = np.arange(1, count + 1)
    return pd.DataFrame(
        {
            "first": first,
            "last": last,
            "email": [
                f"{a}.{b}{i}@example.com".lower() for a, b, i in zip(first, last, ids, strict=True)
            ],
            "phone": [f"+1{n}" for n in rng.integers(2_000_000_000, 9_999_999_999, size=count)],
        }
    )


def format_dates(days: pd.DatetimeIndex, day_index: np.ndarray) -> np.ndarray:
    """Return ISO date strings for each row."""
    return np.asarray(days.strftime("%Y-%m-%d"))[day_index]


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    """Write a CSV with fixed formatting so repeated runs are byte-identical."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, float_format="%.2f", lineterminator="\n")


# ---------------------------------------------------------------- retail

CATEGORIES = ["Electronics", "Home & Garden", "Apparel", "Grocery", "Sports", "Beauty"]
CATEGORY_PRODUCTS = [20, 24, 24, 20, 16, 16]
CATEGORY_PRICE_RANGE = [(150, 900), (30, 250), (20, 120), (3, 30), (20, 200), (8, 60)]
CATEGORY_PREFIX = ["Gadget", "Home", "Apparel", "Grocery", "Sports", "Beauty"]
# R3: Electronics is about half of revenue.
CATEGORY_REVENUE_SHARE = [0.48, 0.17, 0.12, 0.07, 0.09, 0.07]
STORE_SHARE_03 = 0.27  # R1: Store 03 is a large store, so its drop moves the total.
RETAIL_ROWS = 120_000


def build_products(rng: np.random.Generator) -> pd.DataFrame:
    """Create the product dimension. ``list_price`` is used internally and not written."""
    rows = []
    product_id = 1
    for cat, count, (low, high), prefix in zip(
        CATEGORIES, CATEGORY_PRODUCTS, CATEGORY_PRICE_RANGE, CATEGORY_PREFIX, strict=True
    ):
        prices = np.round(np.exp(rng.uniform(np.log(low), np.log(high), size=count)), 2)
        ratios = rng.uniform(0.55, 0.68, size=count)
        for k in range(count):
            rows.append(
                {
                    "product_id": product_id,
                    "product_name": f"{prefix} {k + 1:03d}",
                    "category": cat,
                    "unit_cost": round(float(prices[k] * ratios[k]), 2),
                    "list_price": float(prices[k]),
                }
            )
            product_id += 1
    return pd.DataFrame(rows)


def build_stores() -> pd.DataFrame:
    """Create the eight stores across four regions."""
    regions = ["North", "South", "East", "West"]
    return pd.DataFrame(
        {
            "store_id": np.arange(1, 9),
            "store_name": [f"Store {i:02d}" for i in range(1, 9)],
            "region": [regions[i % 4] for i in range(8)],
        }
    )


def retail_store_weights(days: pd.DatetimeIndex) -> np.ndarray:
    """Per-day store weights. R1: Store 03 sells 40% less from 2026-09-01."""
    base = np.full(8, (1.0 - STORE_SHARE_03) / 7)
    base[2] = STORE_SHARE_03
    factors = np.ones((len(days), 8))
    factors[(days >= pd.Timestamp("2026-09-01")), 2] = 0.6
    return base[None, :] * factors


def retail_category_weights(days: pd.DatetimeIndex, products: pd.DataFrame) -> np.ndarray:
    """Per-day category weights giving the target revenue shares.

    R3 comes from the shares. R7: Home & Garden grows strongly during the last year.
    """
    mean_price = products.groupby("category")["list_price"].mean().reindex(CATEGORIES).to_numpy()
    base = np.asarray(CATEGORY_REVENUE_SHARE) / mean_price
    base = base / base.sum()
    factors = np.ones((len(days), len(CATEGORIES)))
    factors[:, 1] = ramp(days, "2025-09-30", "2026-06-30", 1.40)
    return base[None, :] * factors


def retail_cost_multiplier() -> np.ndarray:
    """R8: cost per sale rises over the last three months, squeezing the gross margin."""
    multiplier = np.ones(LAST_MONTH + 1)
    multiplier[21:24] = [1.009, 1.025, 1.062]
    return multiplier


def build_sales_rows(rng: np.random.Generator, products: pd.DataFrame) -> pd.DataFrame:
    """Create the sales fact table (without customers)."""
    days = build_days()
    store_w = retail_store_weights(days)
    category_w = retail_category_weights(days, products)
    dim_multiplier = store_w.sum(axis=1) * category_w.sum(axis=1)
    weekday = [0.97, 0.97, 0.98, 1.0, 1.08, 1.18, 0.92]
    expected = expected_daily_rows(days, RETAIL_ROWS, weekday, 0.01, dim_multiplier)
    # R2: revenue spike on 2026-08-12 and a collapse on 2026-07-19.
    expected = apply_day_factors(days, expected, {"2026-08-12": 4.0, "2026-07-19": 0.1})
    counts = rng.poisson(expected)
    day_index = np.repeat(np.arange(len(days)), counts)
    n = len(day_index)

    category = sample_groups(rng, day_index, category_w)
    product_index = np.zeros(n, dtype=np.int64)
    for c in range(len(CATEGORIES)):
        mask = category == c
        ids = products.index[products["category"] == CATEGORIES[c]].to_numpy()
        product_index[mask] = rng.choice(ids, size=int(mask.sum()))

    # R4: online orders contain about 2.6 times more items than in-store orders.
    online = rng.random(n) < 0.35
    in_store_qty = rng.choice([1, 2, 3], size=n, p=[0.60, 0.25, 0.15])
    quantity = np.where(online, 1 + rng.poisson(3.0, size=n), in_store_qty)

    gross = products["list_price"].to_numpy()[product_index] * quantity
    rate = rng.choice([0.0, 0.05, 0.10, 0.15], size=n, p=[0.55, 0.2, 0.15, 0.1])
    discount = np.round(gross * rate, 2)
    month = month_numbers(days)[day_index]
    unit_cost = products["unit_cost"].to_numpy()[product_index]
    cost = unit_cost * quantity * retail_cost_multiplier()[month]
    # R6: 30% of discount values are missing.
    discount_column = np.where(rng.random(n) < 0.30, np.nan, discount)
    return pd.DataFrame(
        {
            "sale_id": np.arange(1, n + 1),
            "sale_date": format_dates(days, day_index),
            "store_id": sample_groups(rng, day_index, store_w) + 1,
            "product_id": products["product_id"].to_numpy()[product_index],
            "channel": np.where(online, "online", "in-store"),
            "quantity": quantity,
            "amount": np.round(gross - discount, 2),
            "discount": discount_column,
            "cost_amount": np.round(cost, 2),
            "payment_status": rng.choice(
                ["paid", "refunded", "pending"], size=n, p=[0.90, 0.06, 0.04]
            ),
            "_month": month,
        }
    )


def build_retail_customers(
    rng: np.random.Generator, sales: pd.DataFrame, count: int
) -> pd.DataFrame:
    """Create customers; the signup date is shortly before the first purchase."""
    people = person_columns(rng, count)
    return pd.DataFrame(
        {
            "customer_id": np.arange(1, count + 1),
            "name": (people["first"] + " " + people["last"]).to_numpy(),
            "email": people["email"].to_numpy(),
            "phone": people["phone"].to_numpy(),
            "city": rng.choice(CITIES, size=count),
            "signup_date": dates_before_first_use(rng, sales, "sale_date", count, 60),
            "segment": rng.choice(["Consumer", "Business"], size=count, p=[0.85, 0.15]),
        }
    )


def generate_retail(out_dir: Path) -> None:
    """Write the retail dataset into ``out_dir``."""
    rng = np.random.default_rng(SEED)
    products = build_products(rng)
    sales = build_sales_rows(rng, products)
    # R5: churn is about 25% per month and rises to about 45% in 2026-09.
    churn = churn_schedule(rng, 0.25, 0.45)
    sales["customer_id"] = simulate_customers(rng, sales["_month"].to_numpy(), 6000, churn, 0.75)
    customers = build_retail_customers(rng, sales, 6000)
    columns = [
        "sale_id", "sale_date", "store_id", "product_id", "customer_id", "channel", "quantity",
        "amount", "discount", "cost_amount", "payment_status",
    ]
    write_csv(sales[columns], out_dir / "retail" / "sales.csv")
    write_csv(customers, out_dir / "retail" / "customers.csv")
    write_csv(products.drop(columns=["list_price"]), out_dir / "retail" / "products.csv")
    write_csv(build_stores(), out_dir / "retail" / "stores.csv")


# ---------------------------------------------------------------- bank

CHANNELS = ["mobile", "card", "branch", "atm"]
CHANNEL_WEIGHTS = [0.15, 0.42, 0.23, 0.20]
TXN_TYPES = ["transfer", "payment", "withdrawal", "deposit"]
TXN_TYPE_WEIGHTS = [0.35, 0.40, 0.15, 0.10]
TXN_TYPE_MEDIAN = [300.0, 45.0, 100.0, 600.0]
BRANCH_WEIGHTS = [0.05, 0.20, 0.05, 0.05, 0.18, 0.05, 0.27, 0.05, 0.05, 0.05]
BANK_ROWS = 150_000
BANK_CUSTOMERS = 8000


def build_branches() -> pd.DataFrame:
    """Create ten branches across four regions."""
    regions = ["North", "South", "East", "West"]
    return pd.DataFrame(
        {
            "branch_id": np.arange(1, 11),
            "branch_name": [f"Branch {i:02d}" for i in range(1, 11)],
            "region": [regions[i % 4] for i in range(10)],
        }
    )


def bank_branch_weights(days: pd.DatetimeIndex) -> np.ndarray:
    """Per-day branch weights. B3: three branches hold 65%. B1: Branch 07 loses 35% in 2026-09."""
    base = np.asarray(BRANCH_WEIGHTS)
    factors = np.ones((len(days), len(base)))
    factors[(days >= pd.Timestamp("2026-09-01")), 6] = 0.65
    return base[None, :] * factors


def bank_channel_weights(days: pd.DatetimeIndex) -> np.ndarray:
    """Per-day channel weights. B7: mobile grows strongly during the last year."""
    base = np.asarray(CHANNEL_WEIGHTS)
    factors = np.ones((len(days), len(base)))
    factors[:, 0] = ramp(days, "2025-09-30", "2026-06-30", 1.58)
    return base[None, :] * factors


def failure_probabilities(channel: np.ndarray, month: np.ndarray) -> np.ndarray:
    """B4: failed rate is 2% and then 6% in 2026-09; mobile fails about 3 times as often."""
    is_mobile = channel == 0
    probability = np.zeros(len(channel))
    for m in range(LAST_MONTH + 1):
        rows = month == m
        if not rows.any():
            continue
        target = 0.06 if m == LAST_MONTH else 0.02
        share = is_mobile[rows].mean()
        other_rate = target / (1.0 + 2.0 * share)
        probability[rows] = np.where(is_mobile[rows], 3.0 * other_rate, other_rate)
    return probability


def dates_before_first_use(
    rng: np.random.Generator, used: pd.DataFrame, date_column: str, count: int, max_days: int
) -> np.ndarray:
    """Return an ISO date per customer id 1..count, shortly before that customer's first row.

    Customers without any row get a random date inside the demo period.
    """
    first = pd.to_datetime(used.groupby("customer_id")[date_column].min())
    first = first.reindex(np.arange(1, count + 1)).to_numpy(dtype="datetime64[ns]")
    offset = rng.integers(0, max_days, size=count).astype("timedelta64[D]")
    random_day = np.datetime64(START) + rng.integers(0, 730, size=count).astype("timedelta64[D]")
    chosen = np.where(np.isnat(first), random_day.astype("datetime64[ns]"), first - offset)
    return pd.to_datetime(chosen).strftime("%Y-%m-%d").to_numpy()


def build_bank_customers(
    rng: np.random.Generator, txns: pd.DataFrame, premium: np.ndarray
) -> pd.DataFrame:
    """Create bank customers; the join date is shortly before the first transaction."""
    people = person_columns(rng, BANK_CUSTOMERS)
    return pd.DataFrame(
        {
            "customer_id": np.arange(1, BANK_CUSTOMERS + 1),
            "full_name": (people["first"] + " " + people["last"]).to_numpy(),
            "phone": people["phone"].to_numpy(),
            "city": rng.choice(CITIES, size=BANK_CUSTOMERS),
            "segment": np.where(premium, "premium", "standard"),
            "join_date": dates_before_first_use(rng, txns, "txn_date", BANK_CUSTOMERS, 90),
        }
    )


def build_transactions(rng: np.random.Generator, premium: np.ndarray) -> pd.DataFrame:
    """Create the transactions fact table."""
    days = build_days()
    branch_w = bank_branch_weights(days)
    channel_w = bank_channel_weights(days)
    dim_multiplier = branch_w.sum(axis=1) * channel_w.sum(axis=1)
    weekday = [1.05, 1.05, 1.03, 1.03, 1.10, 0.90, 0.75]
    expected = expected_daily_rows(days, BANK_ROWS, weekday, 0.02, dim_multiplier)
    expected = apply_day_factors(days, expected, {"2026-08-27": 3.0})  # B2: burst of transactions
    counts = rng.poisson(expected)
    day_index = np.repeat(np.arange(len(days)), counts)
    n = len(day_index)
    month = month_numbers(days)[day_index]

    channel = sample_groups(rng, day_index, channel_w)
    # B5: churn is about 22% per month and rises to about 40% in 2026-09.
    churn = churn_schedule(rng, 0.22, 0.40)
    customer = simulate_customers(rng, month, BANK_CUSTOMERS, churn, 0.75)

    txn_type = rng.choice(len(TXN_TYPES), size=n, p=TXN_TYPE_WEIGHTS)
    median = np.asarray(TXN_TYPE_MEDIAN)[txn_type]
    # B8: premium customers have tickets about four times larger.
    scale = np.where(premium[customer - 1], 4.0, 1.0)
    raw_amount = median * np.exp(rng.normal(0.0, 0.7, size=n)) * scale
    amount = np.round(np.clip(raw_amount, 1.0, 500_000.0), 2)

    failed = rng.random(n) < failure_probabilities(channel, month)
    reversed_ = rng.random(n) < 0.015
    status = np.where(failed, "failed", np.where(reversed_, "reversed", "completed"))
    fee = np.select(
        [txn_type == 0, txn_type == 1, txn_type == 2],
        [1.5 + 0.001 * amount, 0.005 * amount, np.full(n, 1.0)],
        default=0.0,
    )
    # B6: 25% of fee values are missing.
    fee = np.where(rng.random(n) < 0.25, np.nan, np.round(fee, 2))
    return pd.DataFrame(
        {
            "txn_id": np.arange(1, n + 1),
            "txn_date": format_dates(days, day_index),
            "customer_id": customer,
            "branch_id": sample_groups(rng, day_index, branch_w) + 1,
            "channel": np.asarray(CHANNELS)[channel],
            "txn_type": np.asarray(TXN_TYPES)[txn_type],
            "amount": amount,
            "status": status,
            "fee": fee,
        }
    )


def generate_bank(out_dir: Path) -> None:
    """Write the bank dataset into ``out_dir``."""
    rng = np.random.default_rng(SEED + 1)
    premium = rng.random(BANK_CUSTOMERS) < 0.15
    txns = build_transactions(rng, premium)
    customers = build_bank_customers(rng, txns, premium)
    write_csv(txns, out_dir / "bank" / "transactions.csv")
    write_csv(customers, out_dir / "bank" / "customers.csv")
    write_csv(build_branches(), out_dir / "bank" / "branches.csv")


def main() -> None:
    """Command line entry point."""
    parser = argparse.ArgumentParser(description="Generate Finalyzer demo data.")
    parser.add_argument("--out", default="data/demo", help="Output directory.")
    args = parser.parse_args()
    out_dir = Path(args.out)
    generate_retail(out_dir)
    generate_bank(out_dir)
    print(f"Demo data written to {out_dir}")


if __name__ == "__main__":
    main()
