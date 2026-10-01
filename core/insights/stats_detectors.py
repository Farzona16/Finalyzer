"""Statistics detectors: outliers, correlation, group differences, skewness, missing values
and trends of numeric columns (spec section 8).

Every test that looks at many columns or pairs is corrected with Benjamini-Hochberg, and a
result must also have a meaningful size (an effect size or a correlation strength), because
with tens of thousands of rows even tiny differences are "significant".
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats

from core.ingest.profiler import quote_identifier
from core.insights.context import InsightContext
from core.insights.ranking import clamp01, confidence
from core.insights.text import humanize
from core.models import ColumnInfo, Finding

MAX_ROWS = 200_000
MAX_NUMERIC = 12
MAX_GROUPS = 12
MIN_GROUP = 20
MIN_ROWS = 50
P_LIMIT = 0.01
OUTLIER_FACTOR = 3.0  # the common 1.5 x IQR fence flags 0.7% of ANY normal column
OUTLIER_MIN_SHARE, OUTLIER_MAX_SHARE, OUTLIER_MIN_COUNT = 0.002, 0.10, 3
CORRELATION_MIN, CORRELATION_N, MAX_CORRELATIONS = 0.5, 30, 3
COHEN_D, ETA_SQUARED, MAX_DIFFERENCES = 0.5, 0.06, 4
SKEW_MIN = 1.0
CRAMER_V, MISSING_MIN_SHARE = 0.2, 0.01
TREND_R2, DAYS_PER_MONTH = 0.05, 30.4375
ID_ROLES = ("order_id", "customer_id")


@dataclass
class StatsData:
    """The columns the tests use, loaded once as a pandas frame."""

    frame: pd.DataFrame
    numeric: list[str]
    categorical: list[str]
    date: str | None
    total_rows: int = 0
    notes: list[str] = field(default_factory=list)


def is_id_like(column: ColumnInfo, rows: int) -> bool:
    """A whole-number column with a different value on (almost) every row is an identifier."""
    return column.dtype == "integer" and rows >= MIN_ROWS and column.distinct >= 0.9 * rows


def pick_columns(ctx: InsightContext, rows: int) -> tuple[list[str], list[str]]:
    """Numeric columns to describe and low-cardinality text columns to group by."""
    skipped = {ctx.roles[r] for r in ID_ROLES if r in ctx.roles}
    usable = [c for c in ctx.model.columns if not c.is_pii and c.name not in skipped]
    numeric = [
        c.name for c in usable if c.dtype in ("integer", "float") and not is_id_like(c, rows)
    ][:MAX_NUMERIC]
    categorical = [c.name for c in usable if c.dtype == "text" and 2 <= c.distinct <= MAX_GROUPS]
    return numeric, categorical


def load_data(ctx: InsightContext) -> StatsData | None:
    """Read the needed columns (a seeded sample when there are more than 200,000 rows)."""
    rows = int(ctx.rows("SELECT COUNT(*) FROM data")[0][0])
    numeric, categorical = pick_columns(ctx, rows)
    date_name = ctx.roles.get("date")
    names = [*numeric, *categorical, *([date_name] if date_name else [])]
    if rows < MIN_ROWS or not numeric:
        return None
    cols = ", ".join(quote_identifier(n) for n in names)
    sample = f" USING SAMPLE reservoir({MAX_ROWS} ROWS) REPEATABLE (42)" if rows > MAX_ROWS else ""
    frame = ctx.con.execute(f"SELECT {cols} FROM data{sample}").df()
    return StatsData(frame, numeric, categorical, date_name, total_rows=rows)


def bh_adjust(p_values: list[float]) -> list[float]:
    """Benjamini-Hochberg adjusted p-values, in the order given."""
    count = len(p_values)
    order = sorted(range(count), key=lambda i: p_values[i])
    adjusted = [0.0] * count
    running = 1.0
    for rank in range(count, 0, -1):
        index = order[rank - 1]
        running = min(running, p_values[index] * count / rank)
        adjusted[index] = running
    return adjusted


def finding(
    kind: str,
    key: str,
    metric: str,
    title: str,
    facts: dict[str, float | str],
    support: int,
    severity: float,
    segment: dict[str, str] | None = None,
) -> Finding:
    """Assemble a finding with the shared bookkeeping."""
    return Finding(
        id=f"{kind}:{key}",
        type=kind,
        metric_id=metric,
        title=title,
        facts=facts,
        segment=segment,
        period_label="all data in view",
        support=support,
        severity=round(clamp01(severity), 4),
        confidence=confidence(None, support),
    )


# ---------------------------------------------------------------------- outliers


def detect_outliers(ctx: InsightContext, data: StatsData) -> list[Finding]:
    """Values far outside the usual range (beyond 3 x IQR from the quartiles)."""
    findings = []
    for name in data.numeric:
        values = data.frame[name].dropna()
        if len(values) < MIN_ROWS:
            continue
        q1, q3 = values.quantile(0.25), values.quantile(0.75)
        iqr = q3 - q1
        if iqr <= 0:
            continue
        low, high = q1 - OUTLIER_FACTOR * iqr, q3 + OUTLIER_FACTOR * iqr
        far = values[(values < low) | (values > high)]
        share = len(far) / len(values)
        if len(far) < OUTLIER_MIN_COUNT or not OUTLIER_MIN_SHARE <= share <= OUTLIER_MAX_SHARE:
            continue
        extreme = float(far.iloc[(far - values.median()).abs().argmax()])
        findings.append(
            finding(
                "outliers",
                name,
                name,
                f"{len(far)} extreme values in {humanize(name)}",
                {
                    "column": name,
                    "count": float(len(far)),
                    "share_pct": round(share * 100, 2),
                    "extreme": round(extreme, 4),
                    "iqr_factor": OUTLIER_FACTOR,
                    "median": round(float(values.median()), 4),
                    "n": float(len(values)),
                },
                len(far),
                0.3 + share * 40,
            )
        )
    return findings


# ---------------------------------------------------------------------- correlation


def pair_values(data: StatsData, a: str, b: str) -> tuple[np.ndarray, np.ndarray]:
    """The rows where both columns have a value."""
    both = data.frame[[a, b]].dropna()
    return both[a].to_numpy(dtype=float), both[b].to_numpy(dtype=float)


def detect_correlation(ctx: InsightContext, data: StatsData) -> list[Finding]:
    """Strong monotonic relations between numeric columns (top 3, BH corrected)."""
    tested = []
    for a, b in itertools.combinations(data.numeric, 2):
        x, y = pair_values(data, a, b)
        if len(x) < CORRELATION_N or np.ptp(x) == 0 or np.ptp(y) == 0:
            continue
        rho, p = stats.spearmanr(x, y)
        tested.append((a, b, float(rho), float(p), float(stats.pearsonr(x, y)[0]), len(x)))
    adjusted = bh_adjust([t[3] for t in tested])
    strong = [
        (t, q)
        for t, q in zip(tested, adjusted, strict=True)
        if abs(t[2]) >= CORRELATION_MIN and q < P_LIMIT
    ]
    strong.sort(key=lambda item: -abs(item[0][2]))
    return [
        finding(
            "correlation",
            f"{a}:{b}",
            a,
            f"{humanize(a)} and {humanize(b)} move together",
            {
                "a": a,
                "b": b,
                "rho": round(rho, 3),
                "pearson": round(r, 3),
                "n": float(n),
                "p_adj": float(f"{q:.3g}"),
                "direction": "up" if rho > 0 else "down",
            },
            n,
            abs(rho),
        )
        for (a, b, rho, _, r, n), q in strong[:MAX_CORRELATIONS]
    ]


# ---------------------------------------------------------------------- group_difference


@dataclass
class GroupTest:
    """The result of comparing a numeric column between the groups of a category column."""

    column: str
    dimension: str
    p: float
    effect: float
    test: str
    high: str
    low: str
    high_mean: float
    low_mean: float
    n: int


def compare_groups(data: StatsData, column: str, dimension: str) -> GroupTest | None:
    """Welch t-test (two groups) or one-way ANOVA (three or more) with an effect size."""
    frame = data.frame[[column, dimension]].dropna()
    groups = {str(k): v[column].to_numpy(dtype=float) for k, v in frame.groupby(dimension)}
    groups = {k: v for k, v in groups.items() if len(v) >= MIN_GROUP}
    if len(groups) < 2 or len(groups) > MAX_GROUPS:
        return None
    means = {k: float(v.mean()) for k, v in groups.items()}
    high, low = max(means, key=means.get), min(means, key=means.get)  # type: ignore[arg-type]
    values = list(groups.values())
    if len(values) == 2:
        p = float(stats.ttest_ind(values[0], values[1], equal_var=False).pvalue)
        pooled = math.sqrt((values[0].var(ddof=1) + values[1].var(ddof=1)) / 2)
        effect, test = (abs(means[high] - means[low]) / pooled if pooled else 0.0), "welch"
    else:
        p = float(stats.f_oneway(*values).pvalue)
        everything = np.concatenate(values)
        total = float(((everything - everything.mean()) ** 2).sum())
        between = sum(len(v) * (v.mean() - everything.mean()) ** 2 for v in values)
        effect, test = (float(between) / total if total else 0.0), "anova"
    return GroupTest(
        column,
        dimension,
        p,
        effect,
        test,
        high,
        low,
        means[high],
        means[low],
        sum(len(v) for v in values),
    )


def big_enough(result: GroupTest) -> bool:
    """A medium effect: Cohen's d >= 0.5 (two groups) or eta squared >= 0.06 (three or more)."""
    return result.effect >= (COHEN_D if result.test == "welch" else ETA_SQUARED)


def detect_group_difference(ctx: InsightContext, data: StatsData) -> list[Finding]:
    """Numeric columns that differ clearly between the groups of a category column."""
    results = [
        r
        for column, dimension in itertools.product(data.numeric, data.categorical)
        if (r := compare_groups(data, column, dimension)) is not None
    ]
    adjusted = bh_adjust([r.p for r in results])
    kept = [(r, q) for r, q in zip(results, adjusted, strict=True) if q < P_LIMIT and big_enough(r)]
    kept.sort(key=lambda item: -item[0].effect)
    return [group_finding(r, q) for r, q in kept[:MAX_DIFFERENCES]]


def group_finding(result: GroupTest, adjusted_p: float) -> Finding:
    """The finding of one significant, sizeable group difference."""
    base = abs(result.low_mean)
    pct = (result.high_mean - result.low_mean) / base * 100 if base > 1e-12 else 0.0
    kind = "d" if result.test == "welch" else "eta2"
    return finding(
        "group_difference",
        f"{result.column}:{result.dimension}",
        result.column,
        f"{humanize(result.column)} differs by {humanize(result.dimension)}: "
        f"{result.high} is higher than {result.low}",
        {
            "metric": result.column,
            "dimension": result.dimension,
            "high": result.high,
            "low": result.low,
            "pct": round(pct, 1),
            "high_mean": round(result.high_mean, 4),
            "low_mean": round(result.low_mean, 4),
            "test": result.test,
            "effect": round(result.effect, 3),
            "effect_kind": kind,
            "p_adj": float(f"{adjusted_p:.3g}"),
            "n": float(result.n),
        },
        result.n,
        result.effect / (1.0 if kind == "d" else 0.3),
        {result.dimension: result.high},
    )


# ---------------------------------------------------------------------- skewness


def detect_skewness(ctx: InsightContext, data: StatsData) -> list[Finding]:
    """Columns with many small values and a few very large ones (positive skew >= 1)."""
    findings = []
    for name in data.numeric:
        values = data.frame[name].dropna().to_numpy(dtype=float)
        if len(values) < MIN_ROWS or len(np.unique(values)) <= 2:
            continue
        skew = float(stats.skew(values))
        if skew < SKEW_MIN:
            continue
        findings.append(
            finding(
                "skewness",
                name,
                name,
                f"{humanize(name)}: most values are small, a few are very large",
                {
                    "column": name,
                    "skew": round(skew, 2),
                    "n": float(len(values)),
                    "median": round(float(np.median(values)), 4),
                    "mean": round(float(values.mean()), 4),
                },
                len(values),
                skew / 4,
            )
        )
    return findings


# ---------------------------------------------------------------------- missing_pattern


def missing_test(
    data: StatsData, column: str, dimension: str
) -> tuple[float, float, str, float, int] | None:
    """Chi-square test of "is empty" against a category: ``(p, V, category, share, n)``."""
    if column == dimension:
        return None
    frame = pd.DataFrame({"empty": data.frame[column].isna(), "group": data.frame[dimension]})
    frame = frame.dropna(subset=["group"])
    table = pd.crosstab(frame["group"], frame["empty"])
    table = table[table.sum(axis=1) >= MIN_GROUP]
    if table.shape[0] < 2 or table.shape[1] < 2:
        return None
    chi2, p, _, _ = stats.chi2_contingency(table, correction=False)
    n = int(table.to_numpy().sum())
    v = math.sqrt(chi2 / (n * (min(table.shape) - 1)))
    shares = table[True] / table.sum(axis=1)
    return float(p), v, str(shares.idxmax()), float(shares.max()), n


def detect_missing_pattern(ctx: InsightContext, data: StatsData) -> list[Finding]:
    """Empty values that cluster in one category (for example one production line)."""
    tests = []
    for column in [*data.numeric, *data.categorical]:
        share = float(data.frame[column].isna().mean())
        if share < MISSING_MIN_SHARE or share > 0.9:
            continue
        for dimension in data.categorical:
            result = missing_test(data, column, dimension)
            if result is not None:
                tests.append((column, dimension, share, *result))
    adjusted = bh_adjust([t[3] for t in tests])
    kept = [
        (t, q) for t, q in zip(tests, adjusted, strict=True) if q < P_LIMIT and t[4] >= CRAMER_V
    ]
    best: dict[str, tuple] = {}
    for item in sorted(kept, key=lambda i: -i[0][4]):
        best.setdefault(item[0][0], item)
    return [
        finding(
            "missing_pattern",
            f"{column}:{dimension}",
            column,
            f"Empty {humanize(column)} values are concentrated in {category}",
            {
                "column": column,
                "dimension": dimension,
                "category": category,
                "pct": round(top * 100, 1),
                "overall_pct": round(share * 100, 1),
                "v": round(v, 3),
                "p_adj": float(f"{q:.3g}"),
                "rows": float(n),
            },
            n,
            v,
            {dimension: category},
        )
        for (column, dimension, share, _, v, category, top, n), q in best.values()
    ]


# ---------------------------------------------------------------------- trend_regression


def detect_trend_regression(ctx: InsightContext, data: StatsData) -> list[Finding]:
    """Numeric columns that rise or fall steadily over time (linear slope, p < 0.01)."""
    if data.date is None:
        return []
    days = pd.to_datetime(data.frame[data.date], errors="coerce")
    tested = []
    for name in data.numeric:
        both = pd.DataFrame({"x": (days - days.min()).dt.days, "y": data.frame[name]}).dropna()
        if len(both) < MIN_ROWS or both["x"].nunique() < 10 or both["y"].nunique() < 3:
            continue
        fit = stats.linregress(both["x"], both["y"])
        tested.append((name, fit, len(both)))
    adjusted = bh_adjust([t[1].pvalue for t in tested])
    return [
        finding(
            "trend_regression",
            name,
            name,
            f"{humanize(name)} {'rises' if fit.slope > 0 else 'falls'} steadily over time",
            {
                "column": name,
                "change": round(float(fit.slope) * DAYS_PER_MONTH, 4),
                "r2": round(float(fit.rvalue) ** 2, 3),
                "p_adj": float(f"{q:.3g}"),
                "n": float(n),
                "direction": "up" if fit.slope > 0 else "down",
            },
            n,
            float(fit.rvalue) ** 2 * 3,
        )
        for (name, fit, n), q in zip(tested, adjusted, strict=True)
        if q < P_LIMIT and float(fit.rvalue) ** 2 >= TREND_R2
    ]


DETECTORS = {
    "outliers": detect_outliers,
    "correlation": detect_correlation,
    "group_difference": detect_group_difference,
    "skewness": detect_skewness,
    "missing_pattern": detect_missing_pattern,
    "trend_regression": detect_trend_regression,
}
