"""Scan an exported audit log (JSONL) for e-mail, phone and card numbers.

Usage: ``python scripts/check_payloads.py finalyzer_audit.jsonl``

Exit code 0: nothing found. 1: at least one hit (the report names the line, purpose and kind,
never the value). 2: the file could not be read as an audit log.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.llm.egress import find_sensitive  # noqa: E402


@dataclass
class Hit:
    """One sensitive value found in one audited payload."""

    line: int
    purpose: str
    kind: str
    preview: str


class AuditFormatError(Exception):
    """A line of the file is not an audit event."""


def mask(value: str) -> str:
    """Show only the first and last character so the report does not repeat the value."""
    return value[0] + "*" * max(len(value) - 2, 1) + value[-1] if len(value) > 2 else "**"


def parse_event(line: str, number: int) -> dict:
    """Decode one JSONL line and check that it looks like an audit event."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError as exc:
        raise AuditFormatError(f"line {number}: not valid JSON") from exc
    if not isinstance(event, dict) or "payload_text" not in event:
        raise AuditFormatError(f"line {number}: no 'payload_text' field")
    return event


def scan_lines(lines: list[str]) -> list[Hit]:
    """All sensitive values in the payloads of the given JSONL lines."""
    hits: list[Hit] = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        event = parse_event(line, number)
        for kind, value in find_sensitive(str(event["payload_text"])):
            hits.append(Hit(number, str(event.get("purpose", "?")), kind, mask(value)))
    return hits


def main() -> int:
    """Read the file, print the report and return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        lines = args.path.read_text(encoding="utf-8").splitlines()
        hits = scan_lines(lines)
    except (OSError, UnicodeDecodeError, AuditFormatError) as exc:
        print(f"Cannot read the audit log: {exc}", file=sys.stderr)
        return 2
    events = sum(1 for line in lines if line.strip())
    for hit in hits:
        print(f"line {hit.line} ({hit.purpose}): {hit.kind} {hit.preview}")
    print(f"{events} payloads scanned, {len(hits)} sensitive values found")
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
