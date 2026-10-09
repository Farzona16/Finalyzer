"""Deterministic row sampling when a table would exceed the memory threshold (spec 2.1)."""

from __future__ import annotations

from typing import Any

from core.runtime.profile import ResourceLimits, load_limits

SAMPLE_SEED = 31
BYTES_PER_CELL = 48  # heuristic for in-memory text cells after cleaning


def sample_badge(fraction: float, locale: str = "en") -> str | None:
    """``Based on a sample (X%)`` when rows were sampled; ``None`` when every row was kept."""
    if fraction >= 0.999:
        return None
    pct = max(1, min(99, round(fraction * 100)))
    if locale == "uz":
        return f"Tanlanma asosida ({pct}%)"
    return f"Based on a sample ({pct}%)"


def estimated_bytes(rows: int, columns: int) -> int:
    return max(0, rows) * max(1, columns) * BYTES_PER_CELL


def maybe_sample_frame(
    frame: Any,
    limits: ResourceLimits | None = None,
    *,
    seed: int = SAMPLE_SEED,
) -> tuple[Any, float]:
    """Keep the whole frame, or a seeded sample. Returns ``(frame, fraction_kept)``."""
    limits = limits or load_limits()
    rows, cols = int(len(frame)), int(getattr(frame, "shape", (0, 1))[1] or 1)
    budget = limits.sample_threshold_mb * 1024 * 1024
    if estimated_bytes(rows, cols) <= budget or rows <= 1:
        return frame, 1.0
    keep = max(1, int(budget / (cols * BYTES_PER_CELL)))
    keep = min(keep, rows)
    if keep >= rows:
        return frame, 1.0
    sampled = frame.sample(n=keep, random_state=seed)
    return sampled.reset_index(drop=True), keep / rows
