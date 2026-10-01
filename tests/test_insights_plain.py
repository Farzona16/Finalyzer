"""U6: frames-based cards, Uzbek/English grounding, polarity, the optional polish, next steps."""

from __future__ import annotations

import json
import re

import pytest

from core.config import Settings
from core.i18n.frames import FINDING_TYPES, LEVELS, FrameError
from core.i18n.polarity import polarity_of
from core.insights import grounding_v3 as g
from core.insights.pipeline import PlainInsights, build_plain_insights
from core.insights.plain import PlainCard, PlainContext, explain, jargon_in, plain_text
from core.insights.polish import polish_card
from core.insights.runner import run_insights
from core.llm.audit import AuditLog
from core.llm.gateway import LLMGateway
from core.metrics.registry import available_metrics
from core.models import Finding
from tests.fakes import FakeProvider, make_settings
from tests.helpers import DemoDataset

DEMOS = (("retail", "retail"), ("bank", "bank"), ("sales", "sales"), ("statistics", "statistics"))
LOCALES = ("uz", "en")


@pytest.fixture(scope="module")
def runs(
    retail, bank, sales, statistics
) -> dict[str, tuple[DemoDataset, list[Finding], PlainContext]]:
    """Findings of every demo dataset (detectors run once for this module)."""
    demos = {"retail": retail, "bank": bank, "sales": sales, "statistics": statistics}
    result = {}
    for name, pack in DEMOS:
        demo = demos[name]
        findings = run_insights(demo.con, demo.model, Settings(_env_file=None), pack).findings
        metrics = {m.id: m for m in available_metrics(demo.model, pack)}
        result[name] = (demo, findings, metrics)
    return result


def all_cards(runs, locale: str, level: str) -> list[tuple[str, Finding, PlainCard]]:
    out = []
    for name, (_, findings, metrics) in runs.items():
        ctx = PlainContext(locale, "UZS", metrics)
        out += [(name, f, explain(f, ctx, level)) for f in findings]
    return out


# ---------------------------------------------------------------- the cards


def test_the_demo_datasets_exercise_every_finding_type(runs) -> None:
    seen = {f.type for _, findings, _ in runs.values() for f in findings}
    assert seen == set(FINDING_TYPES)


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("level", LEVELS)
def test_every_finding_of_every_demo_has_a_card(runs, locale: str, level: str) -> None:
    cards = all_cards(runs, locale, level)
    assert len(cards) >= 40
    for name, finding, card in cards:
        assert card.headline and card.meaning and card.next_step, (name, finding.id)
        for text in card.texts():
            assert "{" not in text and "}" not in text, (name, finding.id, text)
            assert "None" not in text and "nan" not in text.lower().split(), text


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("level", ["simple", "simpler"])
def test_no_jargon_in_simple_texts_of_any_demo(runs, locale: str, level: str) -> None:
    bad = [
        (name, f.id, jargon_in(card))
        for name, f, card in all_cards(runs, locale, level)
        if jargon_in(card)
    ]
    assert bad == []


@pytest.mark.parametrize("locale", LOCALES)
def test_expert_cards_add_details_and_simple_ones_do_not(runs, locale: str) -> None:
    for _, _, card in all_cards(runs, locale, "expert"):
        assert card.details
    for _, _, card in all_cards(runs, locale, "simple"):
        assert card.details == ""


@pytest.mark.parametrize("locale", LOCALES)
def test_the_direction_word_of_a_headline_matches_the_finding(runs, locale: str) -> None:
    for name, finding, card in all_cards(runs, locale, "simple"):
        found = polarity_of(plain_text(card.headline), locale)
        if card.direction in ("up", "down"):
            assert found in (card.direction, None), (name, finding.id, card.headline)
        else:
            assert found is None, (name, finding.id, card.headline)


def test_uzbek_cards_use_the_right_letters_and_number_style(runs) -> None:
    for name, finding, card in all_cards(runs, "uz", "expert"):
        text = " ".join(card.texts())
        assert "\u02bc" not in text and not re.search(r"[oOgG][\u2018\u2019`\u00b4]", text), text
        assert not re.search(r"(?<![\d.\-/])\d{4,}(?![\d\-])", text.replace("\u202f", "")) or all(
            1900 <= int(m) <= 2100 for m in re.findall(r"(?<![\d.\-/])\d{4,}(?![\d\-])", text)
        ), (name, finding.id, text)
        assert not re.search(r"\d\.\d", re.sub(r"\d{4}-\d{2}-\d{2}", "", text)), text


def test_english_cards_use_dots_and_the_same_numbers(runs) -> None:
    uz = {f.id: c for _, f, c in all_cards(runs, "uz", "simple")}
    for _, f, card in all_cards(runs, "en", "simple"):
        assert re.search(r"\d,\d{1,2}(?!\d)", card.headline) is None or "," in uz[f.id].headline


def test_the_two_languages_say_the_same_thing_about_the_same_finding(runs) -> None:
    uz = {f.id: c for _, f, c in all_cards(runs, "uz", "simple")}
    en = {f.id: c for _, f, c in all_cards(runs, "en", "simple")}
    assert uz.keys() == en.keys()
    for key in uz:
        assert uz[key].headline != en[key].headline
        assert (uz[key].direction, uz[key].priority) == (en[key].direction, en[key].priority)


def test_cards_are_deterministic(runs) -> None:
    first = [c.headline for _, _, c in all_cards(runs, "uz", "simple")]
    second = [c.headline for _, _, c in all_cards(runs, "uz", "simple")]
    assert first == second


def test_a_glossary_token_wraps_a_label_that_contains_a_technical_term(runs) -> None:
    cards = all_cards(runs, "en", "simple")
    wrapped = [
        c for _, _, c in cards if "[[win_rate|" in c.headline or "[[gross_margin|" in c.headline
    ]
    assert wrapped
    assert all("[[" not in plain_text(c.headline) for c in wrapped)


def test_a_finding_of_an_unknown_type_is_refused() -> None:
    bad = Finding(id="x", type="mystery", metric_id="m", title="t", facts={})
    with pytest.raises(FrameError):
        explain(bad, PlainContext("en"))


# ---------------------------------------------------------------- grounding


@pytest.mark.parametrize(
    ("text", "locale", "value"),
    [
        ("1 234,56", "uz", 1234.56),
        ("1\u202f234,56", "uz", 1234.56),
        ("1\u00a0234,56", "uz", 1234.56),
        ("12,8 mln", "uz", 12_800_000),
        ("12,8\u00a0mln so\u02bbm", "uz", 12_800_000),
        ("1,2 mlrd", "uz", 1_200_000_000),
        ("5 ming", "uz", 5000),
        ("8,2%", "uz", 8.2),
        ("25 foiz", "uz", 25),
        ("12.8M", "en", 12_800_000),
        ("1,234.56", "en", 1234.56),
        ("8.2%", "en", 8.2),
        ("UZS 4.5K", "en", 4500),
        ("1.234,5", "uz", 1234.5),
    ],
)
def test_numbers_are_read_in_both_styles(text: str, locale: str, value: float) -> None:
    found = g.numbers_in(text, locale)
    assert len(found) == 1 and found[0].value == pytest.approx(value)


def test_a_number_that_is_in_the_original_is_grounded_at_the_shown_precision() -> None:
    assert g.ungrounded("Daromad 12,8 foizga oshdi", "Daromad 12,84 foizga oshdi", "uz") == []
    assert g.ungrounded("It rose 12.8%", "It rose 12.84%", "en") == []
    assert g.ungrounded("It rose 13%", "It rose 12.84%", "en") == []
    assert g.ungrounded("It rose 14%", "It rose 12.84%", "en") == ["14%"]


def test_scales_must_match() -> None:
    assert g.ungrounded("12,8 mln", "12 800 000", "uz") == []
    assert g.ungrounded("12,8 mlrd", "12,8 mln", "uz") == ["12,8 mlrd"]
    assert g.ungrounded("1,2 mln", "1\u202f200\u202f000", "uz") == []


def test_small_numbers_years_dates_and_names_are_not_checked() -> None:
    original = "\u00abStore 03\u00bb 2026-09-12 da 40 foizga oshdi"
    assert (
        g.ungrounded("3 ta guruh, 2026 yil, \u00abStore 07\u00bb, 2026-09-13", original, "uz") == []
    )


def test_a_new_number_is_found() -> None:
    assert g.ungrounded("40 foizga oshdi, 77 ta mijoz", "40 foizga oshdi", "uz") == ["77"]


def test_polarity_flip_is_refused() -> None:
    assert g.polarity_problem("Daromad 40 foizga oshdi", "Daromad 40 foizga kamaydi", "uz")
    assert g.polarity_problem("Revenue rose 40%", "Revenue fell 40%", "en")


def test_hiding_the_direction_is_refused_and_keeping_it_is_fine() -> None:
    assert g.polarity_problem("Daromad 40 foizga oshdi", "Daromad 40 foiz oldi", "uz")
    assert (
        g.polarity_problem("Daromad 40 foizga oshdi", "Daromad 40 foizga ko\u02bbpaydi", "uz")
        is None
    )
    assert g.polarity_problem("Revenue rose 40%", "Revenue increased by 40%", "en") is None
    assert g.polarity_problem("Gap between A and B", "A and B differ a lot", "en") is None


def test_a_direction_word_may_not_appear_from_nowhere() -> None:
    assert g.polarity_problem("Gap between A and B", "A fell behind B", "en")


# ---------------------------------------------------------------- the polish


def good_of(card: PlainCard) -> dict[str, str]:
    """A rewrite that stays grounded, keeps quotes and stays within 120% length."""
    return {
        "headline": card.headline.replace("kamaydi", "pasaydi"),
        "meaning": card.meaning.replace("Bu davom etsa", "Agar davom etsa"),
        "next_step": card.next_step,
    }


@pytest.fixture
def trend_finding() -> Finding:
    return Finding(
        id="trend:win",
        type="trend",
        metric_id="win_rate_pct",
        title="t",
        support=500,
        facts={
            "metric_label": "Yutish ulushi",
            "unit": "percent",
            "last_period": "2026-09",
            "previous_period": "2026-08",
            "last_value": 21.6,
            "previous_value": 31.2,
            "direction": "down",
            "assessment": "bad",
            "change_pp": -9.6,
        },
    )


def uz_card(finding: Finding) -> PlainCard:
    return explain(finding, PlainContext("uz"), "simple")


def polisher(
    reply: dict | str | Exception, **settings
) -> tuple[LLMGateway, FakeProvider, Settings]:
    config = make_settings(uz_polish=True, **settings)
    provider = FakeProvider([json.dumps(reply) if isinstance(reply, dict) else reply])
    return LLMGateway(config, AuditLog(), provider), provider, config


def test_polish_is_off_by_default_and_makes_no_call(trend_finding: Finding) -> None:
    config = make_settings()
    assert config.uz_polish is False
    card = uz_card(trend_finding)
    provider = FakeProvider([json.dumps(good_of(card))])
    gateway = LLMGateway(config, AuditLog(), provider)
    assert polish_card(card, trend_finding, gateway, config) == card
    assert provider.calls == []


def test_a_grounded_rewrite_is_accepted_and_badged(trend_finding: Finding) -> None:
    card = uz_card(trend_finding)
    assert "30,8" in card.headline
    rewrite = good_of(card)
    gateway, provider, config = polisher(rewrite)
    result = polish_card(card, trend_finding, gateway, config)
    assert result.polished and result.headline == rewrite["headline"]
    assert len(provider.calls) == 1


@pytest.mark.parametrize(
    "why",
    [
        "new number",
        "flipped direction",
        "name",
        "jargon",
        "too long",
        "empty",
        "invented number",
    ],
)
def test_a_bad_rewrite_keeps_the_frame_text(trend_finding: Finding, why: str) -> None:
    card = uz_card(trend_finding)
    rewrite = good_of(card)
    change = {
        "new number": {"headline": rewrite["headline"].replace("30,8", "45,1")},
        "flipped direction": {"headline": rewrite["headline"].replace("pasaydi", "oshdi")},
        "name": {
            "headline": rewrite["headline"].replace("\u00abYutish ulushi\u00bb", "Yutish ulushi")
        },
        "jargon": {"meaning": "Bu korrelyatsiya tufayli pasayadi."},
        "too long": {"headline": rewrite["headline"] + " " + rewrite["headline"]},
        "empty": {"headline": ""},
        "invented number": {"meaning": "Agar davom etsa, natija 12 foiz pasayadi."},
    }[why]
    gateway, _, config = polisher({**rewrite, **change})
    result = polish_card(card, trend_finding, gateway, config)
    assert result == card and not result.polished, why


def test_a_provider_error_or_garbage_keeps_the_frame_text(trend_finding: Finding) -> None:
    card = uz_card(trend_finding)
    for reply in (RuntimeError("down"), "not json", "{}"):
        gateway, _, config = polisher(reply)
        assert polish_card(card, trend_finding, gateway, config) == card


def test_only_uzbek_simple_cards_are_polished(trend_finding: Finding) -> None:
    card = uz_card(trend_finding)
    gateway, provider, config = polisher(good_of(card))
    english = explain(trend_finding, PlainContext("en"), "simple")
    assert polish_card(english, trend_finding, gateway, config) == english
    expert = explain(trend_finding, PlainContext("uz"), "expert")
    assert polish_card(expert, trend_finding, gateway, config) == expert
    assert provider.calls == []


def segment_finding(support: int) -> Finding:
    return Finding(
        id="driver:x",
        type="driver",
        metric_id="revenue",
        title="t",
        support=support,
        segment={"store_name": "Secret Store"},
        facts={
            "metric_label": "Revenue",
            "unit": "currency",
            "segment": "Secret Store",
            "share_of_change_pct": 60.0,
            "total_change": -1000.0,
            "direction": "down",
        },
    )


def test_cloud_mode_never_sends_a_group_of_fewer_than_five_rows() -> None:
    finding = segment_finding(support=3)
    card = uz_card(finding)
    gateway, provider, config = polisher(good_of(card), privacy_mode="cloud")
    assert polish_card(card, finding, gateway, config) == card
    assert provider.calls == []


def test_cloud_mode_masks_category_values_and_restores_them() -> None:
    finding = segment_finding(support=50)
    card = uz_card(finding)
    assert "Secret Store" in card.headline
    masked = {
        "headline": card.headline.replace("Secret Store", "Segment A"),
        "meaning": card.meaning,
        "next_step": card.next_step.replace("Secret Store", "Segment A"),
    }
    gateway, provider, config = polisher(masked, privacy_mode="cloud")
    result = polish_card(card, finding, gateway, config)
    sent = json.dumps(provider.calls)
    assert "Secret Store" not in sent and "Segment A" in sent
    assert (
        result.polished and "Secret Store" in result.headline and "Segment A" not in result.headline
    )


def test_the_polish_payload_has_only_the_three_sentences(trend_finding: Finding) -> None:
    card = uz_card(trend_finding)
    gateway, provider, config = polisher(good_of(card))
    polish_card(card, trend_finding, gateway, config)
    user = json.loads(provider.calls[0][-1]["content"])
    assert set(user) == {"headline", "meaning", "next_step"}


# ---------------------------------------------------------------- the whole page


@pytest.mark.parametrize("locale", LOCALES)
def test_the_insights_page_builds_for_every_demo(runs, locale: str) -> None:
    for name, pack in DEMOS:
        demo = runs[name][0]
        page = build_plain_insights(
            demo.con, demo.model, Settings(_env_file=None), pack, locale=locale
        )
        assert isinstance(page, PlainInsights)
        assert len(page.cards) == len(page.report.findings)
        assert page.next_steps.track


def test_next_steps_exist_in_both_languages_with_the_same_structure(runs) -> None:
    demo = runs["statistics"][0]
    uz = build_plain_insights(demo.con, demo.model, Settings(_env_file=None), "statistics", "uz")
    en = build_plain_insights(demo.con, demo.model, Settings(_env_file=None), "statistics", "en")
    assert [
        len(x) for x in (uz.next_steps.missing, uz.next_steps.track, uz.next_steps.quality)
    ] == [len(x) for x in (en.next_steps.missing, en.next_steps.track, en.next_steps.quality)]
    for line in uz.next_steps.missing + uz.next_steps.track + uz.next_steps.quality:
        assert "{" not in line and "}" not in line
    assert uz.next_steps.track != en.next_steps.track


def test_uzbek_next_steps_are_listed_for_review() -> None:
    from pathlib import Path

    review = Path("docs/uz_review.md").read_text(encoding="utf-8")
    assert "next steps" in review and "missing.cost" in review
