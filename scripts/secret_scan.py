"""Scan the repository for likely secrets. Writes reports/secret_scan.txt.

Exits 1 when a high-confidence pattern is found outside allowlisted files.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "secret_scan.txt"
SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "coverage",
}
SKIP_NAMES = {".env", "secret_scan.txt", "security_check.txt"}
# High confidence only: PEM blocks, Google API keys, Slack tokens, GitHub PATs.
PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    re.compile(r"ghp_[0-9A-Za-z]{36}"),
    re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}"),
    re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{20,}"),
]


def should_skip(path: Path) -> bool:
    if path.name in SKIP_NAMES:
        return True
    return any(part in SKIP_DIRS for part in path.parts)


def main() -> int:
    hits: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or should_skip(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in PATTERNS:
            if pattern.search(text):
                hits.append(f"{path.relative_to(ROOT)}: {pattern.pattern[:40]}")
                break
    OUT.parent.mkdir(parents=True, exist_ok=True)
    body = "secret scan\n" + ("\n".join(hits) if hits else "no high-confidence secrets found\n")
    OUT.write_text(body, encoding="utf-8")
    print(body)
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
