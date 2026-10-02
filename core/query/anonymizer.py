"""Request-scoped table/column masking for live NL2SQL (zero identifier leakage).

Real names are rewritten to ``tbl_*`` / ``col_*`` before a prompt leaves the process.
The model’s SQL is restored to original identifiers before validation and DuckDB.
User-facing text is unmasked so the UI never sees tokens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from core.models import DatasetModel

Direction = Literal["mask", "unmask"]
PHYSICAL_TABLES = ("data", "base", "preview")
TABLE_PREFIX = "tbl_"
COLUMN_PREFIX = "col_"
VALUE_PREFIX = "val_"
TABLE_LETTERS = "abcdefghijklmnopqrstuvwxyz"
NUMERIC_VALUE = re.compile(r"^\d+(\.\d+)?$")

# Unquoted SQL keywords and built-ins stay as-is so ``SUM(amount)`` cannot become a token.
SQL_KEYWORDS = frozenset(
    {
        "select", "from", "where", "group", "by", "order", "limit", "as", "and", "or", "not",
        "in", "on", "join", "left", "right", "inner", "outer", "full", "cross", "union", "all",
        "distinct", "having", "case", "when", "then", "else", "end", "is", "null", "like",
        "between", "exists", "with", "recursive", "filter", "over", "partition", "window",
        "cast", "try_cast", "coalesce", "nullif", "true", "false", "asc", "desc", "offset",
        "fetch", "first", "last", "values", "table", "using", "natural", "qualify", "interval",
        "escape", "ilike", "similar", "any", "some", "every", "except", "intersect", "lateral",
        "unnest", "generate_series", "date_trunc", "strftime", "strptime", "extract", "epoch",
        "year", "month", "day", "week", "quarter", "hour", "minute", "second", "count", "sum",
        "avg", "min", "max", "stddev", "variance", "median", "mode", "string_agg", "list",
        "array", "struct", "map", "if", "ifnull", "nvl", "greatest", "least", "abs", "round",
        "floor", "ceil", "ceiling", "power", "sqrt", "ln", "log", "exp", "length", "len",
        "lower", "upper", "trim", "ltrim", "rtrim", "replace", "substring", "substr", "concat",
        "position", "now", "current_date", "current_timestamp", "today", "row_number", "rank",
        "dense_rank", "ntile", "lag", "lead", "first_value", "last_value", "percentile_cont",
        "percentile_disc", "bool_and", "bool_or", "try",
    }
)

TOKEN_RE = re.compile(
    r"\$([^$\s]*)\$.*?\$\1\$"
    r"|'(?:[^']|'')*'"
    r'|"(?:[^"]|"")*"'
    r"|`[^`]*`"
    r"|[^\W\d][\w]*",
    re.UNICODE | re.DOTALL,
)


def _next_table_token(taken: set[str]) -> str:
    for letter in TABLE_LETTERS:
        token = f"{TABLE_PREFIX}{letter}"
        if token not in taken:
            return token
    index = 0
    while True:
        token = f"{TABLE_PREFIX}x{index}"
        if token not in taken:
            return token
        index += 1


def _next_column_token(taken: set[str], start: int) -> tuple[str, int]:
    index = start
    while True:
        token = f"{COLUMN_PREFIX}{index}"
        if token not in taken:
            return token, index + 1
        index += 1


def _next_value_token(taken: set[str], start: int) -> tuple[str, int]:
    index = start
    while True:
        token = f"{VALUE_PREFIX}{index}"
        if token not in taken:
            return token, index + 1
        index += 1


def _collect_values(model: DatasetModel) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for column in model.columns:
        for raw in column.categories or []:
            text = str(raw).strip()
            key = text.lower()
            if not text or key in seen or len(text) < 2 or NUMERIC_VALUE.match(text):
                continue
            seen.add(key)
            values.append(text)
    return values


def _collect_originals(model: DatasetModel) -> list[str]:
    originals: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            originals.append(name)

    for table in PHYSICAL_TABLES:
        add(table)
    add(model.fact_table)
    for table in model.tables:
        add(table)
    for column in model.columns:
        add(column.name)
    for column in model.roles.values():
        add(column)
    return originals


def _split_quoted(raw: str) -> tuple[str, str]:
    """Return ``(quote, inner)``; quote is empty for a bare identifier."""
    if raw.startswith("'") or raw.startswith("$"):
        return "'", raw
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"`":
        inner = raw[1:-1]
        if raw[0] == '"':
            inner = inner.replace('""', '"')
        return raw[0], inner
    return "", raw


def _replace_words(text: str, lookup: dict[str, str]) -> str:
    """Replace whole identifiers (longest first), including those inside quotes."""
    if not text or not lookup:
        return text
    keys = sorted(lookup, key=len, reverse=True)
    pattern = re.compile(r"(?<![\w])(" + "|".join(re.escape(k) for k in keys) + r")(?![\w])", re.I)

    def replace(match: re.Match[str]) -> str:
        return lookup[match.group(1).lower()]

    return pattern.sub(replace, text)


def _requote(quote: str, value: str) -> str:
    if quote == '"':
        return '"' + value.replace('"', '""') + '"'
    if quote == "`":
        return f"`{value}`"
    return value


@dataclass(frozen=True)
class IdentifierMask:
    """Bidirectional map of real identifiers to anonymous tokens for one request."""

    table: str
    forward: dict[str, str]
    reverse: dict[str, str]
    forward_ci: dict[str, str] = field(compare=False)
    reverse_ci: dict[str, str] = field(compare=False)
    value_forward: dict[str, str] = field(default_factory=dict)
    value_reverse: dict[str, str] = field(default_factory=dict)
    value_forward_ci: dict[str, str] = field(default_factory=dict, compare=False)
    value_reverse_ci: dict[str, str] = field(default_factory=dict, compare=False)

    @classmethod
    def from_model(cls, model: DatasetModel) -> IdentifierMask:
        """Stable ``data`` → ``tbl_a``, then ``col_1``… for columns (skip colliding tokens)."""
        originals = _collect_originals(model)
        taken = {name.lower() for name in originals}
        physical = {name.lower() for name in PHYSICAL_TABLES}
        # Source table names (fact + uploaded files) also get tbl_* tokens.
        table_names = physical | {model.fact_table.lower(), *(n.lower() for n in model.tables)}
        forward: dict[str, str] = {}
        reverse: dict[str, str] = {}
        table_token = ""

        for name in originals:
            if name.lower() not in table_names:
                continue
            token = _next_table_token(taken)
            taken.add(token)
            forward[name] = token
            reverse[token] = name
            if name.lower() == "data":
                table_token = token

        index = 1
        for name in originals:
            if name.lower() in table_names:
                continue
            token, index = _next_column_token(taken, index)
            taken.add(token)
            forward[name] = token
            reverse[token] = name

        value_forward: dict[str, str] = {}
        value_reverse: dict[str, str] = {}
        value_index = 1
        for value in _collect_values(model):
            if value.lower() in taken:
                continue
            token, value_index = _next_value_token(taken, value_index)
            taken.add(token)
            value_forward[value] = token
            value_reverse[token] = value

        return cls(
            table=table_token or "tbl_a",
            forward=forward,
            reverse=reverse,
            forward_ci={key.lower(): mapped for key, mapped in forward.items()},
            reverse_ci={key.lower(): mapped for key, mapped in reverse.items()},
            value_forward=value_forward,
            value_reverse=value_reverse,
            value_forward_ci={key.lower(): mapped for key, mapped in value_forward.items()},
            value_reverse_ci={key.lower(): mapped for key, mapped in value_reverse.items()},
        )

    def lookup(self, name: str, direction: Direction) -> str | None:
        """Case-insensitive map lookup."""
        table = self.forward_ci if direction == "mask" else self.reverse_ci
        return table.get(name.lower())

    def transform(self, text: str, direction: Direction) -> str:
        """Rewrite identifiers; skip SQL string literals (including dollar-quotes)."""
        if not text:
            return text

        def replace(match: re.Match[str]) -> str:
            raw = match.group(0)
            quote, inner = _split_quoted(raw)
            if quote == "'":
                return raw
            mapped = self.lookup(inner, direction)
            if mapped is not None:
                if direction == "mask" and not quote and inner.lower() in SQL_KEYWORDS:
                    return raw
                return _requote(quote, mapped)
            if quote == '"' and inner:
                return '"' + self.transform(inner, direction).replace('"', '""') + '"'
            if quote == "`" and inner:
                return "`" + self.transform(inner, direction) + "`"
            return raw

        return TOKEN_RE.sub(replace, text)

    def mask_text(self, text: str) -> str:
        """Replace real table/column names and known category values with tokens."""
        text = self.transform(text, "mask")
        if self.value_forward_ci:
            text = _replace_words(text, self.value_forward_ci)
        return text

    def mask_error(self, text: str) -> str:
        """Remask binder/validator text, including names and values inside quotes."""
        text = _replace_words(text, self.forward_ci)
        if self.value_forward_ci:
            text = _replace_words(text, self.value_forward_ci)
        return text

    def unmask_text(self, text: str) -> str:
        """Replace tokens with the original names and values (safe for SQL and prose)."""
        if self.value_reverse_ci:
            text = _replace_words(text, self.value_reverse_ci)
        return self.transform(text, "unmask")

    def unmask_sql(self, sql: str) -> str:
        """Restore real identifiers in model-written SQL before validation."""
        return self.unmask_text(sql)

    def restore_name(self, token: str) -> str:
        """Unmask one identifier or value token; return ``token`` unchanged otherwise."""
        return (
            self.lookup(token, "unmask")
            or self.value_reverse_ci.get(token.lower())
            or token
        )

    def unmask_spec(self, raw: Any) -> Any:
        """Restore real column names inside an autopilot metric object (and nested ones)."""
        if isinstance(raw, list):
            return [self.unmask_spec(item) for item in raw]
        if not isinstance(raw, dict):
            return raw
        out = dict(raw)
        columns = out.get("columns")
        if isinstance(columns, list):
            out["columns"] = [self.restore_name(c) if isinstance(c, str) else c for c in columns]
        elif isinstance(columns, str):
            out["columns"] = self.restore_name(columns)
        filters = out.get("filter")
        if isinstance(filters, list):
            restored: list[Any] = []
            for clause in filters:
                if not isinstance(clause, dict):
                    restored.append(clause)
                    continue
                item = dict(clause)
                if isinstance(item.get("column"), str):
                    item["column"] = self.restore_name(item["column"])
                value = item.get("value")
                if isinstance(value, str):
                    item["value"] = self.restore_name(value)
                elif isinstance(value, list):
                    item["value"] = [
                        self.restore_name(v) if isinstance(v, str) else v for v in value
                    ]
                restored.append(item)
            out["filter"] = restored
        for key in ("numerator", "denominator"):
            if isinstance(out.get(key), dict):
                out[key] = self.unmask_spec(out[key])
        return out
