"""Closed-set refusal: off-topic, unsafe and prompt-injection questions never become SQL."""

from __future__ import annotations

import re

from core.nlu.normalize import match_key, normalize_text

# Whole-phrase or token patterns on the apostrophe-stripped match key.
UNSAFE = (
    r"\bignore previous\b",
    r"\bignore all\b",
    r"\bdisregard\b",
    r"\bsystem prompt\b",
    r"\bapi key\b",
    r"\bpassword\b",
    r"\bparol\w*",
    r"\bsecret\b",
    r"\bhack\b",
    r"\bexploit\b",
    r"\brm rf\b",
    r"\bdrop table\b",
    r"\bdelete from\b",
    r"\btruncate\b",
    r"\binsert into\b",
    r"\bupdate \w+ set\b",
    r"\bunion select\b",
    r"\bexec(ute)?\b",
    r"\bshell\b",
    r"\bwget \b",
    r"\bcurl \b",
)
OFF_TOPIC = (
    r"\bweather\b",
    r"\bob havo\b",
    r"\brecipe\b",
    r"\bsher\b",
    r"\bpoem\b",
    r"\bjoke\b",
    r"\banekdot\b",
    r"\banecdote\b",
    r"\bcook\b",
    r"\bcapital of\b",
    r"\bwho is the president\b",
    r"\btranslate this\b",
    r"\bwrite (a |an |me )?(poem|story|essay|song)\b",
    r"\bsher yoz\b",
    r"\b2 ?\+ ?2\b",
    r"\bhow are you\b",
    r"\bsalom qalaysiz\b",
)
UNSAFE_RE = re.compile("|".join(UNSAFE))
OFF_TOPIC_RE = re.compile("|".join(OFF_TOPIC))

REFUSE_UZ = "Men faqat shu ma'lumotlarni o\u02bbqib, qisqa javob bera olaman."
REFUSE_EN = "I can only read and summarize this dataset."


def refuse_reason(text: str) -> str | None:
    """``unsafe``, ``off_topic``, or None when the question may be about the data."""
    raw = (text or "").lower()
    key = match_key(normalize_text(text))
    if not key:
        return None
    if UNSAFE_RE.search(key) or UNSAFE_RE.search(raw):
        return "unsafe"
    if OFF_TOPIC_RE.search(key) or "2+2" in raw.replace(" ", "") or OFF_TOPIC_RE.search(raw):
        return "off_topic"
    return None


def refuse_text(locale: str) -> str:
    """The polite refusal shown to the user."""
    return REFUSE_UZ if locale != "en" else REFUSE_EN
