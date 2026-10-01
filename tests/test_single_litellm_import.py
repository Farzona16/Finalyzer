"""Architecture rule: only ``core/llm/providers.py`` may import ``litellm``."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMPORT_RE = re.compile(r"^\s*(import\s+litellm|from\s+litellm\b)", re.MULTILINE)
SKIPPED_DIRS = {".venv", "data", "reports", "__pycache__", ".git", ".ruff_cache", ".pytest_cache"}
ALLOWED = Path("core/llm/providers.py")


def python_files() -> list[Path]:
    """All project Python files outside virtual environments and caches."""
    return [
        path
        for path in ROOT.rglob("*.py")
        if not SKIPPED_DIRS & set(path.relative_to(ROOT).parts)
    ]


def test_litellm_is_imported_only_by_the_provider_module() -> None:
    offenders = [
        path.relative_to(ROOT).as_posix()
        for path in python_files()
        if IMPORT_RE.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [ALLOWED.as_posix()], offenders


def test_importing_the_core_does_not_load_litellm() -> None:
    import subprocess
    import sys

    code = (
        "import sys, core.llm.gateway, core.query.nl2sql;"
        "print('litellm' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, check=True
    )
    assert out.stdout.strip() == "False"
