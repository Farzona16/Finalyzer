"""Spec 5.4 / 5.5: frames exist for every finding x direction x level and follow the rules."""

from __future__ import annotations

import re

import pytest

from core.i18n.frames import (
    FINDING_TYPES,
    LOCALES,
    NUMERIC_PARAMS,
    TEXT_FIELDS,
    FrameError,
    all_frame_keys,
    compare_text,
    load_frames,
    placeholders,
    render_card,
)
from core.i18n.jargon import find_jargon
from core.i18n.polarity import direction_words, polarity_of

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
ALL = [(locale, key) for locale in LOCALES for key in all_frame_keys()]


def split_key(key: str) -> tuple[str, str, str]:
    finding_type, direction, level = key.split(".")
    return finding_type, direction, level


def sentences(text: str) -> list[str]:
    return [part for part in SENTENCE_END.split(text.strip()) if part]


def main_text(frame: dict) -> str:
    return " ".join(str(frame[field]) for field in ("headline", "meaning", "next_step"))


def test_the_matrix_is_what_the_spec_demands() -> None:
    assert len(FINDING_TYPES) == 18  # 8 v2 detectors + 4 sales + 6 statistics
    assert len(all_frame_keys()) == 26 * 3
    for locale in LOCALES:
        assert set(load_frames(locale)) == set(all_frame_keys()), locale


@pytest.mark.parametrize(("locale", "key"), ALL)
def test_every_frame_is_complete_and_uses_only_known_values(locale: str, key: str) -> None:
    finding_type, _, level = split_key(key)
    frame = load_frames(locale)[key]
    for field in ("headline", "meaning", "next_step"):
        assert str(frame[field]).strip(), field
    assert frame["reviewed"] is False  # nothing is human-reviewed yet (listed in docs/uz_review.md)
    spec = FINDING_TYPES[finding_type]
    used = set().union(*(placeholders(str(frame[field])) for field in TEXT_FIELDS))
    assert used <= set(spec.params) | set(spec.expert_params), used
    if level == "expert":
        assert str(frame["details"]).strip()
    else:
        assert frame["details"] == ""
        assert not used & set(spec.expert_params), "expert values belong in details only"


@pytest.mark.parametrize(("locale", "key"), ALL)
def test_sentences_are_short(locale: str, key: str) -> None:
    _, _, level = split_key(key)
    limit = 12 if level == "simpler" else 18
    frame = load_frames(locale)[key]
    for field in ("headline", "meaning", "next_step"):
        for sentence in sentences(str(frame[field])):
            assert len(sentence.split()) <= limit, (key, sentence)


@pytest.mark.parametrize("key", all_frame_keys())
def test_uzbek_values_never_get_a_suffix(key: str) -> None:
    """«{x}» and {x} may be followed by space, punctuation or a closing bracket only."""
    frame = load_frames("uz")[key]
    for field in TEXT_FIELDS:
        text = str(frame[field])
        assert not re.search(r"\}[^\W\d_]", text), (key, text)
        assert not re.search(r"»[^\W\d_]", text), (key, text)
        assert not re.search(r"\}'", text), (key, text)


@pytest.mark.parametrize("key", all_frame_keys())
def test_uzbek_frames_use_the_right_letters(key: str) -> None:
    frame = load_frames("uz")[key]
    for field in TEXT_FIELDS:
        text = str(frame[field])
        assert not re.search(r"[oOgG][\u0027\u2018\u2019\u0060\u00b4]", text), (key, text)
        assert "\u2019" not in text and "\u02bc" not in text, (key, text)


@pytest.mark.parametrize(("locale", "key"), ALL)
def test_direction_words_match_the_finding(locale: str, key: str) -> None:
    _, direction, level = split_key(key)
    frame = load_frames(locale)[key]
    if direction not in ("up", "down"):
        return
    other = "down" if direction == "up" else "up"
    found = direction_words(str(frame["headline"]), locale)
    assert found[direction], f"{key}: the headline needs a {direction} word"
    assert not found[other], f"{key}: the headline has a {other} word"
    assert not direction_words(main_text(frame), locale)[other], key


@pytest.mark.parametrize(("locale", "key"), [item for item in ALL if item[1].endswith("simple")])
def test_simple_text_has_no_jargon(locale: str, key: str) -> None:
    assert find_jargon(main_text(load_frames(locale)[key]), locale) == []


@pytest.mark.parametrize(("locale", "key"), [item for item in ALL if item[1].endswith("simpler")])
def test_simpler_text_has_one_number_at_most(locale: str, key: str) -> None:
    text = main_text(load_frames(locale)[key])
    assert find_jargon(text, locale) == []
    assert not re.search(r"\d", text)
    assert len(placeholders(text) & NUMERIC_PARAMS) <= 1


@pytest.mark.parametrize(("locale", "key"), ALL)
def test_both_languages_use_the_same_placeholders(locale: str, key: str) -> None:
    other = "en" if locale == "uz" else "uz"
    for field in TEXT_FIELDS:
        mine = placeholders(str(load_frames(locale)[key][field]))
        theirs = placeholders(str(load_frames(other)[key][field]))
        if field == "headline" and key.startswith("concentration"):
            continue  # Uzbek uses the ready genitive form, English the short label
        assert mine == theirs or field == "details", (key, field, mine, theirs)


def test_correlation_frames_always_carry_the_caution() -> None:
    for locale, words in (("uz", ("isbotlanmagan", "sabab degani emas")),
                          ("en", ("does not prove", "does not mean"))):
        for direction in ("up", "down"):
            for level in ("simple", "simpler"):
                text = main_text(load_frames(locale)[f"correlation.{direction}.{level}"]).lower()
                assert any(word in text for word in words), (locale, direction, level)


def test_render_fills_values_and_checks_missing_ones() -> None:
    card = render_card(
        "trend", "down", "simple", "uz",
        {"metric": "Savdo", "pct": "8,2", "compare": compare_text("prev_month", "uz")},
    )
    assert card.headline == "«Savdo» o\u02bbtgan oyga nisbatan 8,2 foizga kamaydi."
    assert card.details == ""
    english = render_card(
        "trend", "down", "expert", "en",
        {"metric": "Sales", "pct": "8.2", "compare": compare_text("prev_month", "en"),
         "previous": "10", "current": "9"},
    )
    assert english.headline == '"Sales" fell 8.2% compared with last month.'
    assert "Latest period: 9" in english.details
    with pytest.raises(FrameError):
        render_card("trend", "down", "simple", "uz", {"metric": "Savdo"})
    with pytest.raises(FrameError):
        render_card("trend", "sideways", "simple", "uz", {})
    with pytest.raises(FrameError):
        load_frames("de")


def test_the_spec_example_frames_are_present_in_spirit() -> None:
    uz = load_frames("uz")
    trend_down = uz["trend.down.simple"]["headline"]
    assert trend_down.startswith("«{metric}» {compare} {pct} foizga kamaydi")
    assert "{metric_gen}" in uz["concentration.neutral.simple"]["headline"]
    assert "{date} kuni" in uz["anomaly.down.simple"]["headline"]
    assert "qaytib kelmadi" in uz["churn.up.simple"]["headline"]
    assert uz["data_quality.neutral.simple"]["headline"].startswith("«{column}» ustunida")
    assert "{ratio} baravar" in uz["segment_gap.neutral.simple"]["headline"]


def test_polarity_helper() -> None:
    assert polarity_of("Savdo o'sdi", "uz") == "up"
    assert polarity_of("Savdo o\u02bbsdi", "uz") == "up"
    assert polarity_of("Savdo kamaydi", "uz") == "down"
    assert polarity_of("Savdo oshdi, lekin foyda kamaydi", "uz") == "mixed"
    assert polarity_of("Savdo bor", "uz") is None
    assert polarity_of("Sales fell 5%", "en") == "down"
    assert polarity_of("Sales improved", "en") == "up"
