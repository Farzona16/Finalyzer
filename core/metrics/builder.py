"""Turn a metric expression with role placeholders into DuckDB SQL."""

from __future__ import annotations

import re

from core.errors import ValidationError
from core.ingest.profiler import quote_identifier
from core.ingest.roles import ROLE_SYNONYMS
from core.models import Metric

PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def placeholders(expr: str) -> list[str]:
    """Return the role names used in an expression, in order of appearance."""
    return PLACEHOLDER.findall(expr)


def expression_sql(expr: str, roles: dict[str, str]) -> str:
    """Replace ``{role}`` placeholders with quoted column names.

    Unknown placeholders and unmapped roles raise ``ValidationError``.
    """

    def replace(match: re.Match[str]) -> str:
        role = match.group(1)
        if role not in ROLE_SYNONYMS:
            raise ValidationError(f"Unknown placeholder {{{role}}} in metric expression.")
        if role not in roles:
            raise ValidationError(f"The role '{role}' is not mapped to a column.")
        return quote_identifier(roles[role])

    sql = PLACEHOLDER.sub(replace, expr)
    if "{" in sql or "}" in sql:
        raise ValidationError("Curly braces are only allowed in {role} placeholders.")
    return sql


def metric_sql(metric: Metric, roles: dict[str, str]) -> str:
    """Return the SQL aggregate expression of a metric for the given role mapping."""
    return expression_sql(metric.expr, roles)
