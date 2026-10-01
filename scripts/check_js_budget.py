"""Fail the build when the initial JS (scripts in dist/index.html) exceeds 350 KB gzipped.

Run after ``npm run build`` in frontend/: ``python scripts/check_js_budget.py``.
"""

from __future__ import annotations

import gzip
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "frontend" / "dist"
LIMIT = 350 * 1024
SCRIPT = re.compile(r'<script[^>]+src="([^"]+)"')


def main() -> int:
    html = DIST / "index.html"
    if not html.is_file():
        print("frontend/dist/index.html missing; run npm run build first", file=sys.stderr)
        return 2
    total = 0
    lines: list[str] = []
    for src in SCRIPT.findall(html.read_text(encoding="utf-8")):
        path = DIST / src.lstrip("/")
        if not path.is_file() or path.suffix != ".js":
            continue
        gz = len(gzip.compress(path.read_bytes(), compresslevel=9))
        total += gz
        lines.append(f"{src}: {gz} bytes gzipped")
    print("\n".join(lines))
    print(f"initial JS gzipped: {total} / {LIMIT} bytes")
    if total > LIMIT:
        print("budget exceeded", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
