"""Static library of generic action templates per finding type (and direction).

Templates only use placeholders that are filled from the finding's facts, so they never add
numbers of their own. Causes are always written as hypotheses.
"""

from __future__ import annotations

import string

from core.models import Finding

# Keys: (finding type, assessment or direction). ``*`` matches anything.
ACTION_TEMPLATES: dict[tuple[str, str], list[str]] = {
    ("trend", "bad"): [
        "Compare {metric} by store, product and channel for {period} to see where it moved most.",
        "Check for known causes such as stock-outs, price changes or paused campaigns.",
    ],
    ("trend", "good"): [
        "Find out which activities coincided with the improvement in {metric} and repeat them.",
        "Check that the change is not a one-off before raising targets.",
    ],
    ("trend", "neutral"): [
        "Review whether the change in {metric} is expected for the season.",
    ],
    ("driver", "bad"): [
        "Investigate {segment} first: it explains {share}% of the change.",
        "Compare {segment} with the previous month day by day to find when it changed.",
    ],
    ("driver", "good"): [
        "Study what changed in {segment}, which explains {share}% of the improvement.",
    ],
    ("driver", "neutral"): [
        "Look at {segment} first: it explains {share}% of the change.",
    ],
    ("anomaly", "*"): [
        "Check whether {date} matches a campaign, an outage or a data import.",
        "If the day is genuine, note it before comparing periods; if not, correct the source data.",
    ],
    ("concentration", "*"): [
        "Reduce dependence on {segment} by testing offers in the next-largest {dimension} groups.",
        "Track the share of {segment} every month to see whether concentration is growing.",
    ],
    ("segment_gap", "*"): [
        "Find out what makes {high} different from {low} and test whether {low} can adopt it.",
        "Check that the gap is not caused by a different mix of products or customers.",
    ],
    ("segment_momentum", "up"): [
        "Check whether the growth of {segment} is planned and whether supply can keep up.",
        "Test whether the approach used for {segment} works in other {dimension} groups.",
    ],
    ("segment_momentum", "down"): [
        "Investigate why {segment} is growing more slowly than the rest of the business.",
    ],
    ("churn", "bad"): [
        "Ask a sample of customers who did not return why they left.",
        "Test a win-back message for customers who were active last month and then stopped.",
    ],
    ("churn", "good"): [
        "Find out what kept more customers active and keep doing it.",
    ],
    ("data_quality", "*"): [
        "Ask the data owner to fill or explain the gap in {column}.",
        "Treat any analysis that uses {column} with caution until it is fixed.",
    ],
}


def template_key(finding: Finding) -> tuple[str, str]:
    """The key used to look up the templates of a finding."""
    facts = finding.facts
    if finding.type in ("trend", "driver", "churn"):
        return finding.type, str(facts.get("assessment", "neutral"))
    if finding.type == "segment_momentum":
        return finding.type, str(facts.get("direction", "up"))
    return finding.type, "*"


def fill(template: str, values: dict[str, str]) -> str | None:
    """Fill a template; ``None`` when a placeholder has no value."""
    names = {name for _, name, _, _ in string.Formatter().parse(template) if name}
    if not names <= values.keys():
        return None
    return template.format(**values)


def actions_for(finding: Finding, values: dict[str, str]) -> list[str]:
    """One or two filled action templates for a finding (at least one)."""
    templates = ACTION_TEMPLATES.get(template_key(finding)) or ACTION_TEMPLATES.get(
        (finding.type, "*"), []
    )
    filled = [text for t in templates if (text := fill(t, values))]
    return (filled or ["Review this finding with the data owner."])[:2]
