"""Write docs/uz_review.md: all Uzbek strings that a native speaker has not reviewed yet."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.i18n.review import write  # noqa: E402

if __name__ == "__main__":
    print(f"written: {write()}")
