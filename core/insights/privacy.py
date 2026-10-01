"""What findings may be sent to a model.

In ``cloud`` mode findings that rest on fewer than ``K_ANON_MIN`` rows are dropped, and
category values are replaced by neutral labels unless the operator allowed them (D7).
"""

from __future__ import annotations

import re
import string

from core.config import Settings
from core.models import Finding

SEGMENT_KEY = re.compile(r"segment")
LABELS = string.ascii_uppercase


def is_small(finding: Finding, settings: Settings) -> bool:
    """True when a cloud-mode finding rests on too few rows to leave the machine."""
    return settings.privacy_mode == "cloud" and finding.support < settings.k_anon_min


def strip_small_groups(finding: Finding, settings: Settings) -> Finding:
    """Remove the runner-up category from a finding when it rests on too few rows."""
    if settings.privacy_mode != "cloud":
        return finding
    support = finding.facts.get("second_support")
    if support is None or float(support) >= settings.k_anon_min:
        return finding
    kept = {k: v for k, v in finding.facts.items() if not k.startswith("second_")}
    return finding.model_copy(update={"facts": kept})


def cloud_safe(findings: list[Finding], settings: Settings) -> list[Finding]:
    """Findings that may be shown to a model: no tiny groups (cloud mode only)."""
    kept = [f for f in findings if not is_small(f, settings)]
    return [strip_small_groups(f, settings) for f in kept]


class SegmentMasker:
    """Replaces category values by ``Segment A``, ``Segment B`` ... and maps them back."""

    def __init__(self) -> None:
        self._forward: dict[str, str] = {}

    def label(self, value: str) -> str:
        """The neutral label for a category value (created on first use)."""
        if value not in self._forward:
            index = len(self._forward)
            suffix = LABELS[index % 26] + (str(index // 26) if index >= 26 else "")
            self._forward[value] = f"Segment {suffix}"
        return self._forward[value]

    def mask_finding(self, finding: Finding) -> Finding:
        """A copy of the finding without category values in facts, title or segment."""
        values = self.segment_values(finding)
        facts = {
            k: (self.label(v) if isinstance(v, str) and v in values else v)
            for k, v in finding.facts.items()
        }
        segment = {k: self.label(v) for k, v in (finding.segment or {}).items()} or None
        title = self.mask_text(finding.title, values)
        return finding.model_copy(update={"facts": facts, "segment": segment, "title": title})

    def mask_text(self, text: str, values: set[str]) -> str:
        """Replace every known category value inside a text."""
        for value in sorted(values, key=len, reverse=True):
            text = text.replace(value, self.label(value))
        return text

    @staticmethod
    def segment_values(finding: Finding) -> set[str]:
        """Category values that occur in a finding."""
        values = {v for v in (finding.segment or {}).values()}
        values |= {
            v for k, v in finding.facts.items() if isinstance(v, str) and SEGMENT_KEY.search(k)
        }
        return {v for v in values if v}

    def unmask(self, text: str) -> str:
        """Put the real category values back into text returned by a model."""
        for value, label in sorted(self._forward.items(), key=lambda kv: -len(kv[1])):
            text = text.replace(label, value)
        return text


def prepare_for_model(
    findings: list[Finding], settings: Settings
) -> tuple[list[Finding], SegmentMasker | None]:
    """Apply suppression and (when category values are not allowed) masking."""
    safe = cloud_safe(findings, settings)
    if settings.category_values_allowed:
        return safe, None
    masker = SegmentMasker()
    return [masker.mask_finding(f) for f in safe], masker
