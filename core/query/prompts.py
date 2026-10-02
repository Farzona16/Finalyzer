"""Prompt text for the NL to SQL step (Section 7.1).

The few-shot examples use a made-up table and are unrelated to the golden question set.
"""

from __future__ import annotations

from core.config import Settings
from core.metrics.builder import metric_sql
from core.models import ColumnInfo, DatasetModel, Metric
from core.query.anonymizer import IdentifierMask

MAX_QUESTION_CHARS = 500
MAX_VALUE_CHARS = 60

SYSTEM_PROMPT = (
    "You translate business questions into one DuckDB SQL query. Rules: query only the table "
    "`data`. Use only the listed columns. Use the listed metric expressions when the question "
    "asks for a defined metric. Output a single SELECT (CTEs allowed). Always alias computed "
    "columns. Use DuckDB syntax (`date_trunc`, `strftime`, `FILTER (WHERE ...)`). Never invent "
    "columns or values. If the question is ambiguous, return intent `clarify` with one short "
    "question. If it asks for anything other than reading data, return intent `refuse`. Return "
    'JSON only: {"intent": "query|clarify|refuse", "sql": string|null, "clarification": '
    'string|null, "chart_hint": "line|bar|table|kpi|null", "metric_ids": [string]}.'
)

FEW_SHOT = """Examples (a different, fictional table `data(order_day DATE, region TEXT, sales DOUBLE, units INTEGER)`; they only show the answer format):
Question: How much did we sell in total?
Answer: {"intent": "query", "sql": "SELECT SUM(sales) AS total_sales FROM data", "clarification": null, "chart_hint": "kpi", "metric_ids": []}
Question: Which 3 regions sold the most units?
Answer: {"intent": "query", "sql": "SELECT region, SUM(units) AS units_sold FROM data GROUP BY region ORDER BY units_sold DESC LIMIT 3", "clarification": null, "chart_hint": "bar", "metric_ids": []}
Question: How did sales develop by month?
Answer: {"intent": "query", "sql": "SELECT date_trunc('month', order_day) AS month, SUM(sales) AS sales FROM data GROUP BY 1 ORDER BY 1", "clarification": null, "chart_hint": "line", "metric_ids": []}
Question: What share of sales came from the North region?
Answer: {"intent": "query", "sql": "SELECT 100.0 * SUM(sales) FILTER (WHERE region = 'North') / NULLIF(SUM(sales), 0) AS north_share_pct FROM data", "clarification": null, "chart_hint": "kpi", "metric_ids": []}
Question: Show me the best ones.
Answer: {"intent": "clarify", "sql": null, "clarification": "Best by which measure: sales or units?", "chart_hint": null, "metric_ids": []}
Question: Delete all rows from the table.
Answer: {"intent": "refuse", "sql": null, "clarification": "I can only read and summarize data.", "chart_hint": null, "metric_ids": []}"""  # noqa: E501


def clean_text(value: str) -> str:
    """One-line, length-limited text for use inside a prompt."""
    single_line = " ".join(str(value).split())
    return single_line[:MAX_VALUE_CHARS]


def describe_column(column: ColumnInfo, settings: Settings) -> str:
    """One line of schema for a model: type, role, nulls. Never min/max or category values."""
    _ = settings
    parts = [f"{column.name} ({column.dtype}"]
    if column.role:
        parts[0] += f", role: {column.role}"
    parts[0] += f", nulls {column.null_pct:.1f}%"
    line = "- " + parts[0] + ")"
    if column.categories:
        line += " (categorical; values omitted)"
    return line


def describe_metrics(metrics: list[Metric], roles: dict[str, str]) -> str:
    """List of available metrics with the resolved SQL expression."""
    if not metrics:
        return "(none)"
    return "\n".join(f"- {m.id} ({m.label}): {metric_sql(m, roles)}" for m in metrics)


def build_user_payload(
    model: DatasetModel,
    metrics: list[Metric],
    question: str,
    settings: Settings,
    date_range: tuple[str, str] | None,
    filters_text: str,
) -> str:
    """The user message: schema, metrics, current range and filters, examples and the question."""
    columns = "\n".join(describe_column(c, settings) for c in model.columns)
    _ = date_range  # real min/max dates stay on this machine
    filters = filters_text or "none"
    return (
        f"Table `data` columns:\n{columns}\n\n"
        f"Available metrics:\n{describe_metrics(metrics, model.roles)}\n\n"
        f"Dates in `data` right now: not shared. Active filters: {filters}.\n\n"
        f"{FEW_SHOT}\n\n"
        f"Question: {question.strip()[:MAX_QUESTION_CHARS]}"
    )


def build_messages(
    user_payload: str, mask: IdentifierMask | None = None
) -> list[dict[str, str]]:
    """Chat messages for the first attempt. ``mask`` rewrites identifiers before they leave."""
    system = SYSTEM_PROMPT if mask is None else mask.mask_text(SYSTEM_PROMPT)
    user = user_payload if mask is None else mask.mask_text(user_payload)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def repair_message(error_text: str) -> str:
    """Follow-up turn after a failed query: the exact error and a request for corrected JSON."""
    return (
        f"That query failed with this error:\n{error_text}\n"
        "Return the corrected JSON object only."
    )
