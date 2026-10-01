"""Spec 5.2 / 5.5 / 5.7: apostrophes, glossary, jargon lint and synonyms."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from core.i18n import glossary, synonyms
from core.i18n.glossary import explain, find_entry, load_glossary
from core.i18n.jargon import find_jargon
from core.i18n.synonyms import role_for_column, stage_rank
from core.ingest.roles import ROLE_SYNONYMS
from core.nlu.normalize import (
    APOSTROPHES,
    match_key,
    normalize_apostrophes,
    normalize_text,
    to_display_orthography,
)

PLACEHOLDER = re.compile(r"\{(\w+)\}")
WRONG_UZ_LETTER = re.compile(r"[oOgG][\u0027\u2018\u2019\u0060\u00b4]")

# ---------------------------------------------------------------- apostrophes


@pytest.mark.parametrize("apostrophe", list(APOSTROPHES))
def test_every_apostrophe_variant_matches(apostrophe: str) -> None:
    typed = f"o{apostrophe}tgan oy"
    assert normalize_text(typed) == "o'tgan oy"
    assert match_key(typed) == match_key("otgan oy") == "otgan oy"


def test_the_spec_examples_all_match() -> None:
    variants = ["o'tgan oy", "o\u02bbtgan oy", "o\u2018tgan oy", "O\u2019TGAN OY", "oʻtgan  oy?"]
    assert len({normalize_text(text) for text in variants}) == 1


def test_apostrophe_normalizer_can_target_any_character() -> None:
    assert normalize_apostrophes("g\u2018alaba", "\u02bb") == "g\u02bbalaba"
    assert normalize_apostrophes("g\u2018alaba") == "g'alaba"


def test_normalize_text_basics() -> None:
    assert normalize_text("  Savdo,  JAMI!!  ") == "savdo jami"
    assert normalize_text("sotuv_sanasi") == "sotuv sanasi"
    assert normalize_text("") == ""
    assert normalize_text(None) == ""  # type: ignore[arg-type]
    assert normalize_text("«Mas’ul»  menejer") == "mas'ul menejer"


def test_lowercase_is_safe_for_the_dotted_capital_i() -> None:
    assert normalize_text("\u0130STANBUL") == "istanbul"
    assert normalize_text("ISH") == "ish"


def test_numbers_and_percent_signs_survive_normalization() -> None:
    assert normalize_text("8,2% oʻsdi, 1.5 baravar.") == "8,2% o'sdi 1.5 baravar"
    assert normalize_text("narx, 5. Qadam") == "narx 5 qadam"


def test_stray_apostrophes_and_quotes_are_dropped() -> None:
    assert normalize_text("'savdo'") == "savdo"
    assert normalize_text("a ' b") == "a b"


def test_match_key_ignores_missing_apostrophes_and_cyrillic() -> None:
    assert match_key("Mas'ul") == match_key("masul") == match_key("Масул")
    assert match_key("сумма") == match_key("summa")
    assert match_key("дата") == match_key("data")
    assert match_key("жами") == match_key("jami")
    assert match_key("қиймат") == match_key("qiymat")


def test_display_orthography_uses_the_right_letters() -> None:
    assert to_display_orthography("o'tgan g'alaba to'g'ri") == "oʻtgan gʻalaba toʻgʻri"
    assert to_display_orthography("O‘zbekiston G`ayrat") == "Oʻzbekiston Gʻayrat"
    assert to_display_orthography("ma'lumot mas'ul sa'y") == "ma'lumot mas'ul sa'y"
    assert to_display_orthography("'quoted'") == "'quoted'"
    assert to_display_orthography("") == ""


def test_display_orthography_is_idempotent() -> None:
    once = to_display_orthography("o'zbek tilida g‘oya")
    assert to_display_orthography(once) == once


# ---------------------------------------------------------------- glossary


@pytest.mark.parametrize("locale", ["uz", "en"])
def test_glossary_has_enough_complete_entries(locale: str) -> None:
    entries = load_glossary(locale)
    assert len(entries) >= 150
    for entry in entries.values():
        assert entry.term.strip() and entry.plain.strip(), entry.key
        assert entry.reviewed is False


def test_glossary_languages_have_identical_keys() -> None:
    assert set(load_glossary("uz")) == set(load_glossary("en"))


def test_glossary_uzbek_letters_are_correct() -> None:
    for entry in load_glossary("uz").values():
        for text in (entry.term, entry.plain):
            assert not WRONG_UZ_LETTER.search(text), (entry.key, text)
            assert "\u02bc" not in text and "\u2019" not in text, (entry.key, text)


def test_the_spec_examples_are_in_the_glossary() -> None:
    uz = load_glossary("uz")
    assert uz["churn"].plain == "oldin xarid qilgan, lekin endi qilmayotgan mijozlar"
    assert uz["anomaly"].plain == "odatdagidan keskin farq"
    assert uz["concentration"].plain == "bir narsaga haddan tashqari bog\u02bbliqlik"
    assert uz["correlation"].plain == "ikki ko\u02bbrsatkichning birga o\u02bbzgarishi"
    assert uz["median"].plain == "o\u02bbrtadagi qiymat"
    assert uz["standard_deviation"].plain == "qiymatlar qanchalik tarqoq"
    assert uz["margin"].plain == "foydaning savdodagi ulushi"
    assert uz["win_rate"].plain == "yutib olingan bitimlar ulushi"


@pytest.mark.parametrize(
    ("text", "locale", "key"),
    [
        ("churn", "uz", "churn"),
        ("Mediana", "uz", "median"),
        ("o'rtadagi", "uz", None),
        ("mas'ul", "uz", "owner"),
        ("masul", "uz", "owner"),
        ("p-qiymat", "uz", "p_value"),
        ("P-VALUE", "uz", "p_value"),
        ("отток", "uz", "churn"),
        ("standard deviation", "en", "standard_deviation"),
        ("Win Rate", "en", "win_rate"),
        ("win_rate", "en", "win_rate"),
        ("no such term", "en", None),
    ],
)
def test_glossary_lookup(text: str, locale: str, key: str | None) -> None:
    entry = find_entry(text, locale)
    assert (entry.key if entry else None) == key


def test_explain_returns_the_plain_text_or_none() -> None:
    assert explain("anomaliya", "uz") == "odatdagidan keskin farq"
    assert explain("anomaly", "en") == "a value that is far from the usual"
    assert explain("zzz", "uz") is None


def test_glossary_module_rejects_unknown_locales() -> None:
    with pytest.raises(ValueError):
        glossary.load_glossary("ru")


# ---------------------------------------------------------------- jargon lint


@pytest.mark.parametrize(
    ("text", "locale", "expected"),
    [
        ("Mijozlar ketishi oshdi (churn).", "uz", ["churn"]),
        ("Savdo anomaliyasi topildi", "uz", ["anomaliya"]),
        ("Mediana va dispersiya hisoblandi", "uz", ["mediana", "dispersiya"]),
        ("p-qiymat 0,01", "uz", ["p-qiymat"]),
        ("Standart ogʻish katta", "uz", ["standart ogish"]),
        ("The z-score is high and the p-value is low", "en", ["p-value", "z-score"]),
        ("Churn rose; check the median", "en", ["churn", "median"]),
        ("HHI is 0.4", "en", ["hhi"]),
        ("Savdo oʻsdi, foyda kamaydi.", "uz", []),
        ("Sales rose and costs fell.", "en", []),
    ],
)
def test_jargon_is_found(text: str, locale: str, expected: list[str]) -> None:
    assert sorted(find_jargon(text, locale)) == sorted(expected)


def test_a_term_inside_a_glossary_token_is_allowed() -> None:
    assert find_jargon("Bu [[churn|mijozlar ketishi]] belgisi.", "uz") == []
    assert find_jargon("This is [[anomaly|an anomaly]] in sales.", "en") == []
    assert find_jargon("[[churn|churn]] va churn", "uz") == ["churn"]


def test_jargon_check_rejects_unknown_locales() -> None:
    with pytest.raises(ValueError):
        find_jargon("x", "ru")


# ---------------------------------------------------------------- synonyms


def test_every_v2_synonym_is_kept() -> None:
    data = synonyms.load()["roles"]
    for role, words in ROLE_SYNONYMS.items():
        assert set(words) <= set(data[role]["en"]), role


def test_every_role_has_all_three_languages() -> None:
    for role, by_language in synonyms.load()["roles"].items():
        for language in ("uz", "ru", "en"):
            assert by_language[language], (role, language)


def test_the_synonym_file_has_the_roles_of_appendix_c() -> None:
    assert set(synonyms.load()["roles"]) == {
        "date", "amount", "cost", "quantity", "customer_id", "order_id", "status", "stage",
        "owner", "source", "target",
    }


@pytest.mark.parametrize(
    ("column", "role"),
    [
        ("sana", "date"), ("Sotuv_sanasi", "date"), ("дата", "date"), ("order_date", "date"),
        ("summa", "amount"), ("сумма", "amount"), ("Jami", "amount"), ("revenue", "amount"),
        ("tannarx", "cost"), ("себестоимость", "cost"), ("cogs", "cost"),
        ("miqdor", "quantity"), ("шт", "quantity"), ("qty", "quantity"),
        ("mijoz_id", "customer_id"), ("клиент", "customer_id"), ("client_id", "customer_id"),
        ("buyurtma_id", "order_id"), ("номер_заказа", "order_id"), ("deal_id", "order_id"),
        ("holat", "status"), ("статус", "status"),
        ("bosqich", "stage"), ("этап", "stage"), ("stage", "stage"),
        ("mas'ul", "owner"), ("masul", "owner"), ("менеджер", "owner"), ("rep", "owner"),
        ("manba", "source"), ("источник", "source"),
        ("reja", "target"), ("план", "target"), ("quota", "target"),
    ],
)
def test_columns_map_to_roles(column: str, role: str) -> None:
    assert role_for_column(column) == role


def test_a_longer_column_name_is_found_by_its_words() -> None:
    assert role_for_column("sotuv sanasi 2026") == "date"
    assert role_for_column("Mijoz ID raqami") == "customer_id"


def test_unknown_columns_have_no_role() -> None:
    for name in ("", "xyz", "foo_bar", "x"):
        assert role_for_column(name) is None


@pytest.mark.parametrize(
    ("name", "rank"),
    [
        ("lead", 0), ("so'rov", 0), ("лид", 0),
        ("qualified", 1), ("saralangan", 1),
        ("proposal", 2), ("taklif", 2), ("предложение", 2),
        ("negotiation", 3), ("muzokara", 3), ("переговоры", 3),
        ("won", 4), ("yutildi", 4), ("выиграно", 4),
        ("lost", 4), ("yutqazildi", 4), ("проиграно", 4),
    ],
)
def test_stage_order(name: str, rank: int) -> None:
    assert stage_rank(name) == rank


def test_unknown_stage_has_no_rank() -> None:
    assert stage_rank("on hold") is None


def test_the_data_files_are_valid_json_with_the_right_letters() -> None:
    folder = Path(synonyms.DATA_FILE).parent
    for name in ("synonyms_uz_ru_en.json", "glossary_uz.json", "frames_uz.json"):
        text = (folder / name).read_text(encoding="utf-8")
        json.loads(text)
        assert not WRONG_UZ_LETTER.search(text), name
