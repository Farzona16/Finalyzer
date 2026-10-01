"""Guess column roles (date, amount, cost, ...) from names and types (Appendix B)."""

from __future__ import annotations

from core.models import ColumnInfo

ROLE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "date": (
        "date", "order_date", "sale_date", "txn_date", "transaction_date", "created_at",
        "timestamp", "day", "period",
    ),
    "amount": (
        "amount", "revenue", "sales", "total", "total_amount", "price_total", "value", "sum",
        "turnover",
    ),
    "cost": ("cost", "cost_amount", "cogs", "expense", "unit_cost_total"),
    "quantity": ("quantity", "qty", "units", "count_items"),
    "customer_id": (
        "customer_id", "client_id", "cust_id", "customer", "client", "user_id", "account_id",
    ),
    "order_id": ("order_id", "sale_id", "txn_id", "transaction_id", "invoice_id", "id"),
    "status": ("status", "state", "payment_status", "txn_status"),
}
NUMERIC_ROLES = {"amount", "cost", "quantity"}
# "id" is far too short to be used for contains-matching.
EXACT_ONLY = {"id"}


def type_fits(role: str, column: ColumnInfo) -> bool:
    """Numeric roles need numbers, the date role needs a date column."""
    if role in NUMERIC_ROLES:
        return column.dtype in ("integer", "float")
    if role == "date":
        return column.dtype == "date"
    return True


def token_match(synonym: str, name: str) -> bool:
    """True when the synonym's words appear in the name as a run of whole words."""
    words, target = name.split("_"), synonym.split("_")
    return any(words[i : i + len(target)] == target for i in range(len(words) - len(target) + 1))


def pick_column(role: str, candidates: list[ColumnInfo]) -> str | None:
    """Pick the best column for a role: exact synonym match first, then contains-match."""
    by_name = {c.name.lower(): c for c in candidates if type_fits(role, c)}
    for synonym in ROLE_SYNONYMS[role]:
        if synonym in by_name:
            return by_name[synonym].name
    for synonym in ROLE_SYNONYMS[role]:
        if synonym in EXACT_ONLY:
            continue
        for name, column in by_name.items():
            if token_match(synonym, name):
                return column.name
    return None


def detect_roles(columns: list[ColumnInfo]) -> dict[str, str]:
    """Return ``{role: column}``. PII columns are ignored; missing roles are simply absent."""
    candidates = [c for c in columns if not c.is_pii]
    roles: dict[str, str] = {}
    for role in ROLE_SYNONYMS:
        used = set(roles.values())
        column = pick_column(role, [c for c in candidates if c.name not in used])
        if column:
            roles[role] = column
    return roles
