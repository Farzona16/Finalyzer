"""Grok smoke test (needs XAI_API_KEY and GROK_MODEL_*): ``python scripts/grok_smoke.py``."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from provider_smoke import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main("grok"))
