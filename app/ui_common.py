"""Small UI helpers shared by all tabs: safe text, error boundary, password gate, badges."""

from __future__ import annotations

import hmac
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import streamlit as st

from app.i18n import t
from app.state import AUTH_KEY
from app.theme import ACCENT, GOOD, NEUTRAL, badge_html
from core.config import Settings
from core.errors import describe_exception

MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]<>~$#])")
PRIVACY_COLORS = {"demo": ACCENT, "cloud": ACCENT, "local": GOOD}


def md_escape(text: object) -> str:
    """Escape Markdown and LaTeX characters so data values and model text show as plain text."""
    return MARKDOWN_SPECIAL.sub(r"\\\1", str(text))


@contextmanager
def error_boundary(area: str) -> Iterator[None]:
    """Show a readable message (with a reference id for unknown errors) instead of a crash.

    Streamlit's own control-flow exceptions are not ``Exception`` subclasses, so reruns and
    stops pass through untouched.
    """
    try:
        yield
    except Exception as exc:  # noqa: BLE001 - the whole point is to catch everything
        st.error(t("error.area", area=area, message=describe_exception(exc)))


def password_ok(settings: Settings, typed: str) -> bool:
    """Constant-time comparison with the configured password (D14)."""
    return hmac.compare_digest(typed.encode("utf-8"), settings.app_password.encode("utf-8"))


def password_gate(settings: Settings, on_ok: Callable[[], None] | None = None) -> bool:
    """True when the visitor may continue. Draws the password box otherwise."""
    if not settings.app_password or st.session_state.get(AUTH_KEY):
        return True
    typed = st.text_input(t("auth.prompt"), type="password", key="fz_password")
    if typed and password_ok(settings, typed):
        st.session_state[AUTH_KEY] = True
        if on_ok:
            on_ok()
        return True
    if typed:
        st.error(t("auth.wrong"))
    return False


def privacy_badge(settings: Settings) -> str:
    """HTML of the privacy-mode pill for the sidebar."""
    label = t(f"privacy.{settings.privacy_mode}")
    return badge_html(label, PRIVACY_COLORS.get(settings.privacy_mode, NEUTRAL))


def mode_banner(settings: Settings) -> None:
    """Warn visitors of the public demo and cloud modes not to upload real personal data."""
    if settings.privacy_mode in ("demo", "cloud"):
        st.warning(t("banner.no_personal_data"))
