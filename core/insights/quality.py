"""Data-quality findings: mostly empty columns and gaps in the dates."""

from __future__ import annotations

from core.ingest.profiler import quote_identifier
from core.insights.context import InsightContext
from core.insights.ranking import clamp01, confidence
from core.metrics.timeintel import date_column
from core.models import Finding

NULL_SHARE = 0.20
DATE_GAP_DAYS = 7


def detect_null_columns(ctx: InsightContext) -> list[Finding]:
    """Columns of the flat view where at least 20% of the values are empty."""
    names = [c.name for c in ctx.model.columns if not c.is_pii]
    counts = ", ".join(f"COUNT({quote_identifier(n)})" for n in names)
    row = ctx.rows(f"SELECT COUNT(*), {counts} FROM data")[0]
    total = int(row[0])
    if total == 0:
        return []
    findings = []
    for name, non_null in zip(names, row[1:], strict=True):
        share = 1 - int(non_null) / total
        if share < NULL_SHARE:
            continue
        findings.append(
            Finding(
                id=f"data_quality:{name}", type="data_quality", metric_id="data_quality",
                title=f"Column {name} is {share * 100:.0f}% empty",
                facts={"column": name, "null_pct": round(share * 100, 1), "rows": float(total)},
                period_label="all data in view", support=total, severity=round(clamp01(share), 4),
                confidence=confidence(None, total),
            )
        )
    return findings


def detect_date_gaps(ctx: InsightContext) -> list[Finding]:
    """The longest stretch of days without any row, when it is longer than a week."""
    if "date" not in ctx.roles:
        return []
    day = date_column(ctx.roles)
    sql = (
        "SELECT prev_day, day, day - prev_day FROM ("
        f"SELECT day, LAG(day) OVER (ORDER BY day) AS prev_day FROM "
        f"(SELECT DISTINCT {day} AS day FROM data WHERE {day} IS NOT NULL)"
        ") WHERE prev_day IS NOT NULL ORDER BY 3 DESC LIMIT 1"
    )
    rows = ctx.rows(sql)
    if not rows or int(rows[0][2]) <= DATE_GAP_DAYS:
        return []
    start, end, gap = rows[0][0], rows[0][1], int(rows[0][2])
    total = int(ctx.rows("SELECT COUNT(*) FROM data")[0][0])
    return [
        Finding(
            id="data_quality:date_gaps", type="data_quality", metric_id="data_quality",
            title=f"No data for {gap - 1} days between {start.isoformat()} and {end.isoformat()}",
            facts={"gap_days": float(gap - 1), "gap_start": start.isoformat(),
                   "gap_end": end.isoformat()},
            period_label=f"{start.isoformat()} to {end.isoformat()}", support=total,
            severity=round(clamp01((gap - 1) / 30), 4), confidence=confidence(None, total),
        )
    ]
