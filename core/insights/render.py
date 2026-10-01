"""Plain-English wording of findings for the template narrator (no LLM involved).

Every number in the text is a formatted copy of a fact, so the result passes the grounding
check. Causes are hypotheses ("may", "likely") and never promise outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.insights.actions import actions_for
from core.insights.text import humanize, pct, value_text, verb
from core.models import Finding


@dataclass
class CardParts:
    """The text parts of a card before priority and confidence are added."""

    observation: str
    evidence: list[str]
    likely_cause: str
    actions: list[str]
    track_metric: str


def f_str(finding: Finding, key: str, default: str = "") -> str:
    """A string fact."""
    return str(finding.facts.get(key, default))


def f_num(finding: Finding, key: str) -> float:
    """A numeric fact."""
    return float(finding.facts[key])


def money(finding: Finding, key: str) -> str:
    """A fact formatted with the finding's unit."""
    return value_text(abs(f_num(finding, key)), f_str(finding, "unit", "count"))


def trend_parts(f: Finding) -> CardParts:
    """Wording for a month-over-month change."""
    label, direction = f_str(f, "metric_label"), f_str(f, "direction")
    last, previous = f_str(f, "last_period"), f_str(f, "previous_period")
    if "change_pp" in f.facts:
        size = f"{abs(f_num(f, 'change_pp')):.1f} points"
        head = f"{label} {verb(direction)} {size} in {last} compared with {previous}."
        threshold = None
    else:
        size = pct(f_num(f, "change_pct"))
        head = f"{label} {verb(direction)} {size} in {last} compared with {previous}."
        threshold = f"The usual month-to-month swing is below {pct(f_num(f, 'threshold_pct'))}."
    evidence = [
        f"{label} was {money(f, 'last_value')} in {last} versus {money(f, 'previous_value')} "
        f"in {previous}."
    ]
    if threshold:
        evidence.append(threshold)
    if "yoy_pct" in f.facts:
        way = "higher" if f_num(f, "yoy_pct") > 0 else "lower"
        yoy = pct(f_num(f, "yoy_pct"))
        evidence.append(f"Against the same month a year earlier it is {yoy} {way}.")
    if "incomplete_period" in f.facts:
        evidence.append(
            f"{f_str(f, 'incomplete_period')} is not finished yet, so the last full month is used."
        )
    cause = (
        "This may come from a few segments rather than the whole business, or from seasonality, "
        "pricing or promotions; the data alone cannot confirm the cause."
    )
    values = {"metric": label.lower(), "period": f"{last}"}
    return CardParts(head, evidence, cause, actions_for(f, values), label)


def anomaly_parts(f: Finding) -> CardParts:
    """Wording for a single unusual day."""
    label, day = f_str(f, "metric_label"), f_str(f, "date")
    word = "spiked" if f_str(f, "kind") == "spike" else "dropped"
    head = f"{label} {word} on {day}."
    if "ratio_x" in f.facts:
        head = f"{label} {word} on {day}: {f_num(f, 'ratio_x'):.1f}x the level expected."
    evidence = [f"{label} on {day} was {money(f, 'value')}."]
    if "expected_value" in f.facts:
        expected = money(f, "expected_value")
        evidence.append(f"The expected level for that weekday is about {expected}.")
    evidence.append(f"Robust z-score: {abs(f_num(f, 'z_score')):.1f}.")
    cause = (
        "A one-day jump or dip may be explained by a promotion, an outage, a batch import "
        "or a data-entry error."
    )
    return CardParts(head, evidence, cause, actions_for(f, {"date": day}), label)


def churn_parts(f: Finding) -> CardParts:
    """Wording for the customer churn rate."""
    last, direction = f_str(f, "last_period"), f_str(f, "direction")
    head = (
        f"Customer churn {verb(direction)} to {f_num(f, 'churn_pct'):.1f}% in {last}, "
        f"{abs(f_num(f, 'change_pp')):.1f} points {'above' if direction == 'up' else 'below'} "
        f"the recent average of {f_num(f, 'baseline_churn_pct'):.1f}%."
    )
    evidence = [
        "Churn is the share of customers active in the previous month who did not return.",
        f"New customers made up {f_num(f, 'new_customer_share_pct'):.1f}% of the active customers.",
    ]
    cause = (
        "Customers may be leaving because of service, prices or competitors; "
        "the data alone cannot tell which."
    )
    return CardParts(head, evidence, cause, actions_for(f, {}), "Customer churn rate")


def concentration_parts(f: Finding) -> CardParts:
    """Wording for dependence on one or a few categories."""
    label, dimension = f_str(f, "metric_label"), humanize(f_str(f, "dimension"))
    segment = f_str(f, "top_segment")
    head = (
        f"{segment} accounts for {f_num(f, 'top1_share_pct'):.0f}% of {label.lower()}, and the "
        f"top 3 {dimension} values together hold {f_num(f, 'top3_share_pct'):.0f}%."
    )
    evidence = [
        f"Share of the largest {dimension} group: {f_num(f, 'top1_share_pct'):.1f}%.",
        f"Share of the three largest groups: {f_num(f, 'top3_share_pct'):.1f}%.",
    ]
    cause = (
        "Dependence on one group may reflect the product mix, the store network or "
        "limited activity in the other groups."
    )
    values = {"segment": segment, "dimension": dimension}
    return CardParts(head, evidence, cause, actions_for(f, values), label)


def driver_parts(f: Finding) -> CardParts:
    """Wording for the category that explains a change."""
    label, segment = f_str(f, "metric_label"), f_str(f, "segment")
    what = "increase" if f_str(f, "direction") == "up" else "decrease"
    share = f"{f_num(f, 'share_of_change_pct'):.0f}"
    head = f"{segment} explains {share}% of the {what} in {label.lower()}."
    evidence = [
        f"{segment} changed by {money(f, 'segment_change')} while the total changed by "
        f"{money(f, 'total_change')}.",
        f"{segment} made up {f_num(f, 'segment_level_share_pct'):.0f}% of {label.lower()} in "
        f"{f_str(f, 'previous_period')}.",
    ]
    if "second_segment" in f.facts:
        evidence.append(
            f"{f_str(f, 'second_segment')} explains a further "
            f"{f_num(f, 'second_share_of_change_pct'):.0f}%."
        )
    cause = (
        f"Something specific to {segment} may be behind this, such as supply, staffing, "
        "local competition or a price change; the data does not show which."
    )
    actions = actions_for(f, {"segment": segment, "share": share})
    return CardParts(head, evidence, cause, actions, label)


def gap_parts(f: Finding) -> CardParts:
    """Wording for the difference between the best and worst category."""
    label, dimension = f_str(f, "metric_label"), humanize(f_str(f, "dimension"))
    high, low = f_str(f, "high_segment"), f_str(f, "low_segment")
    if "ratio_x" in f.facts:
        head = f"{label} for {high} is {f_num(f, 'ratio_x'):.1f}x the value for {low}."
    else:
        head = f"{label} differs strongly between {high} and {low}."
    evidence = [
        f"{high}: {money(f, 'high_value')}; {low}: {money(f, 'low_value')} (by {dimension}).",
        f"Rows behind each value: {f_num(f, 'high_support'):,.0f} "
        f"and {f_num(f, 'low_support'):,.0f}.",
    ]
    cause = (
        "A different customer mix, price level or process may explain the gap; "
        "the data alone cannot confirm it."
    )
    values = {"high": high, "low": low}
    return CardParts(head, evidence, cause, actions_for(f, values), label)


def momentum_parts(f: Finding) -> CardParts:
    """Wording for a category growing faster or slower than the whole."""
    label, segment = f_str(f, "metric_label"), f_str(f, "segment")
    dimension = humanize(f_str(f, "dimension"))
    growth = f_num(f, "growth_pct")
    head = (
        f"{segment} {'grew' if growth >= 0 else 'changed by'} {abs(growth):.0f}% year on year "
        f"in {f_str(f, 'window')}, against {f_num(f, 'overall_growth_pct'):.0f}% overall."
    )
    evidence = [
        f"{label} for {segment}: {money(f, 'current_value')} now versus "
        f"{money(f, 'previous_value')} a year earlier.",
        f"The gap to the overall growth is {abs(f_num(f, 'gap_pp')):.0f} points.",
    ]
    cause = (
        "Growth this different from the average may reflect a campaign, a change in demand "
        "or seasonality; the data alone cannot confirm which."
    )
    values = {"segment": segment, "dimension": dimension}
    return CardParts(head, evidence, cause, actions_for(f, values), label)


def quality_parts(f: Finding) -> CardParts:
    """Wording for empty columns and date gaps."""
    if "column" in f.facts:
        column = f_str(f, "column")
        head = f"Column {column} is {f_num(f, 'null_pct'):.0f}% empty."
        evidence = [f"{f_num(f, 'null_pct'):.1f}% of {f_num(f, 'rows'):,.0f} rows have no value."]
        values = {"column": column}
    else:
        head = f"There is a gap of {f_num(f, 'gap_days'):.0f} days without data."
        evidence = [f"No rows between {f_str(f, 'gap_start')} and {f_str(f, 'gap_end')}."]
        values = {"column": "the date column"}
    cause = "Values may not be recorded for every row, or an export step may have dropped them."
    return CardParts(head, evidence, cause, actions_for(f, values), "Data completeness")


BUILDERS = {
    "trend": trend_parts,
    "anomaly": anomaly_parts,
    "churn": churn_parts,
    "concentration": concentration_parts,
    "driver": driver_parts,
    "segment_gap": gap_parts,
    "segment_momentum": momentum_parts,
    "data_quality": quality_parts,
}


def describe(finding: Finding) -> CardParts:
    """Card text for any finding type."""
    return BUILDERS[finding.type](finding)
