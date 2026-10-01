"""scripts/check_payloads.py finds personal data in an exported audit log."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from core.errors import PrivacyBlockedError
from core.llm.audit import AuditLog
from core.llm.gateway import LLMGateway
from scripts.check_payloads import AuditFormatError, mask, scan_lines
from tests.fakes import FakeProvider, make_settings, query_reply

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_payloads.py"


def event_line(payload: str, purpose: str = "sql") -> str:
    """One JSONL line shaped like an exported AuditEvent."""
    return json.dumps({"purpose": purpose, "payload_text": payload, "blocked": False})


def run_script(path: Path) -> subprocess.CompletedProcess[str]:
    """Run the script as a user would."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True, timeout=60,
        check=False,
    )


@pytest.mark.parametrize(
    "payload, kind",
    [
        ("mail anna.smith@example.com now", "email"),
        ("call +998 90 123 45 67 today", "phone number"),
        ("call (415) 555-2671 today", "phone number"),
        ("card 4111 1111 1111 1111 used", "card number"),
        ("card 4111111111111111 used", "card number"),
    ],
)
def test_each_kind_is_found_and_masked(payload: str, kind: str) -> None:
    hits = scan_lines([event_line(payload)])
    assert [h.kind for h in hits] == [kind]
    assert hits[0].line == 1 and hits[0].purpose == "sql"
    assert "smith" not in hits[0].preview and "1111 1111" not in hits[0].preview


def test_clean_payloads_have_no_hits() -> None:
    lines = [event_line("SELECT SUM(amount) FROM data WHERE year = 2026 AND id = 1234567")]
    assert scan_lines(lines) == []
    assert scan_lines(["", event_line("nothing here"), "  "]) == []


def test_line_numbers_point_at_the_right_event() -> None:
    lines = [event_line("fine"), event_line("x@y.org", "narrate"), event_line("fine")]
    hit = scan_lines(lines)[0]
    assert (hit.line, hit.purpose) == (2, "narrate")


@pytest.mark.parametrize("bad", ["not json", "[1, 2]", json.dumps({"purpose": "x"})])
def test_malformed_lines_are_reported(bad: str) -> None:
    with pytest.raises(AuditFormatError):
        scan_lines([bad])


def test_mask_never_returns_the_value() -> None:
    assert mask("abcdef") == "a****f"
    assert mask("ab") == "**"


def test_script_exit_codes(tmp_path: Path) -> None:
    clean = tmp_path / "clean.jsonl"
    clean.write_text(event_line("hello") + "\n", encoding="utf-8")
    dirty = tmp_path / "dirty.jsonl"
    dirty.write_text(event_line("write to a@b.org") + "\n", encoding="utf-8")
    broken = tmp_path / "broken.jsonl"
    broken.write_text("oops\n", encoding="utf-8")
    assert run_script(clean).returncode == 0
    found = run_script(dirty)
    assert found.returncode == 1 and "email" in found.stdout and "a@b.org" not in found.stdout
    assert run_script(broken).returncode == 2
    assert run_script(tmp_path / "missing.jsonl").returncode == 2


def test_exported_audit_of_a_real_session_is_clean(retail) -> None:
    """A blocked question and a normal one: the export contains no personal data at all."""
    settings = make_settings()
    gateway = LLMGateway(settings, AuditLog(), FakeProvider([query_reply("SELECT 1")]))
    with pytest.raises(PrivacyBlockedError):
        gateway.complete_text("sql", [{"role": "user", "content": "mail anna@example.com"}])
    gateway.complete_text("sql", [{"role": "user", "content": "How many sales?"}])
    exported = gateway.audit.export_jsonl().splitlines()
    assert len(exported) == 2
    assert scan_lines(exported) == []

