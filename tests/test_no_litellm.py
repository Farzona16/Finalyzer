"""Architecture rules for providers (v3.1-Z spec 2.5 and 3.1).

``litellm`` is gone; the Anthropic SDK loads only when an Anthropic provider is built, so
offline mode and ``/health`` never import it.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMPORT_RE = re.compile(r"^\s*(import\s+litellm|from\s+litellm\b)", re.MULTILINE)
SKIPPED_DIRS = {".venv", "data", "reports", "__pycache__", ".git", ".ruff_cache",
                ".pytest_cache", "node_modules"}


def python_files() -> list[Path]:
    return [
        path
        for path in ROOT.rglob("*.py")
        if not SKIPPED_DIRS & set(path.relative_to(ROOT).parts)
    ]


def test_nothing_imports_litellm() -> None:
    offenders = [
        path.relative_to(ROOT).as_posix()
        for path in python_files()
        if IMPORT_RE.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_litellm_is_not_a_dependency() -> None:
    for name in ("requirements.txt", "backend/requirements.txt", "requirements.lock.txt"):
        text = (ROOT / name).read_text(encoding="utf-8").lower()
        assert "litellm" not in text, name


def test_importing_the_core_loads_no_provider_sdk() -> None:
    code = (
        "import sys, core.llm.gateway, core.query.nl2sql, core.llm.registry;"
        "print(any(m in sys.modules for m in ('litellm', 'anthropic', 'openai')))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, check=True
    )
    assert out.stdout.strip() == "False"
