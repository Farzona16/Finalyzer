"""Confidence and ranking of findings: ``score = severity x confidence``."""

from __future__ import annotations

from core.models import Finding, Level

TOP_COUNT = 5
HIGH_CONFIDENCE = 0.7
MEDIUM_CONFIDENCE = 0.4
HIGH_PRIORITY = 0.5
MEDIUM_PRIORITY = 0.2


def confidence(n_periods: float | None = None, support: float | None = None) -> float:
    """``min(1, n_periods/12) x min(1, support/100)``; a missing input counts as 1."""
    periods = 1.0 if n_periods is None else min(1.0, n_periods / 12)
    rows = 1.0 if support is None else min(1.0, support / 100)
    return round(periods * rows, 4)


def clamp01(value: float) -> float:
    """Limit a number to the range 0..1."""
    return max(0.0, min(1.0, value))


def score(finding: Finding) -> float:
    """Ranking score of a finding."""
    return finding.severity * finding.confidence


def rank_findings(findings: list[Finding]) -> list[Finding]:
    """Findings sorted by score (best first); ties keep the detectors' order."""
    return sorted(findings, key=score, reverse=True)


def split_top(
    findings: list[Finding], count: int = TOP_COUNT
) -> tuple[list[Finding], list[Finding]]:
    """The top findings and the rest (already ranked)."""
    ranked = rank_findings(findings)
    return ranked[:count], ranked[count:]


def confidence_level(finding: Finding) -> Level:
    """High / Medium / Low from the numeric confidence."""
    if finding.confidence >= HIGH_CONFIDENCE:
        return "High"
    return "Medium" if finding.confidence >= MEDIUM_CONFIDENCE else "Low"


def priority_level(finding: Finding) -> Level:
    """High / Medium / Low from the ranking score."""
    value = score(finding)
    if value >= HIGH_PRIORITY:
        return "High"
    return "Medium" if value >= MEDIUM_PRIORITY else "Low"
