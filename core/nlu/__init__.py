"""Natural-language helpers: normalization now; Ask v2 lives in ``core.nlu.ask``."""

from core.nlu.normalize import match_key, normalize_text, to_display_orthography

__all__ = ["match_key", "normalize_text", "to_display_orthography"]
