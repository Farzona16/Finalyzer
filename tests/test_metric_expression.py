"""User-edited metric formulas are validated before they are used."""

from __future__ import annotations

import pytest

from core.errors import ValidationError
from core.metrics.registry import validate_expression


def test_valid_formula_is_accepted(retail) -> None:
    text = "SUM({amount}) / NULLIF(COUNT(DISTINCT {order_id}), 0)"
    assert validate_expression(retail.con, retail.model, text) == text


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "x" * 600,
        "SUM({amount}); DROP TABLE base",
        "SUM({amount}) -- comment",
        "SUM({amount}) /* c */",
        "{amount}",  # not an aggregate
        "SUM({unknown_role})",
        "SUM({amount}) FROM base",
        "SUM({amount}) FROM data WHERE 1=1",
        "SUM(no_such_column)",
        "SUM({amount}) } {",
        "(SELECT 1)",
    ],
)
def test_bad_formula_is_rejected(retail, text: str) -> None:
    with pytest.raises(ValidationError):
        validate_expression(retail.con, retail.model, text)
