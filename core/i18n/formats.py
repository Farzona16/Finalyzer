"""Number, date and currency formatting for Uzbek and English (spec 5.3).

Uzbek: groups of digits are separated by a narrow no-break space, the decimal mark is a comma,
a unit word is separated from its number by a no-break space (so a line never breaks between
them). English: comma groups, decimal point, units glued to the number.

Rounding is always "half up" (12.25 -> 12.3), never "to even", and works on ``Decimal`` so
there are no float surprises. The same rules are implemented in ``frontend/src/lib/format.ts``
and both sides are tested against ``shared/format_cases.json``.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

NNBSP = "\u202f"  # narrow no-break space: digit group separator in Uzbek
NBSP = "\u00a0"  # no-break space: between a number and its unit
EMPTY = "\u2013"  # shown for a missing value
MINUS = "-"  # plain hyphen-minus: pastes into spreadsheets and parses back

LOCALES = ("uz", "en")

MONTHS = {
    "uz": (
        "yanvar", "fevral", "mart", "aprel", "may", "iyun",
        "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr",
    ),
    "en": (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ),
}
MONTHS_SHORT_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
WEEKDAYS = {  # Monday first, like ``date.weekday()``
    "uz": ("dushanba", "seshanba", "chorshanba", "payshanba", "juma", "shanba", "yakshanba"),
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
}

# (threshold, Uzbek word, English letter), smallest first.
COMPACT_UNITS_ASCENDING = (
    (Decimal(10) ** 3, "ming", "K"),
    (Decimal(10) ** 6, "mln", "M"),
    (Decimal(10) ** 9, "mlrd", "B"),
    (Decimal(10) ** 12, "trln", "T"),
)

# currency -> (decimals when written in full, Uzbek form, English form)
CURRENCIES = {
    "UZS": (0, "so\u02bbm", "UZS"),
    "USD": (2, "$", "$"),
    "EUR": (2, "\u20ac", "\u20ac"),
    "RUB": (2, "\u20bd", "\u20bd"),
}


def _check_locale(locale: str) -> None:
    if locale not in LOCALES:
        raise ValueError(f"unsupported locale: {locale!r}")


def _to_decimal(value: object) -> Decimal | None:
    """A ``Decimal`` for numbers; ``None`` for missing or non-finite values."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _round(value: Decimal, digits: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)


def _group(digits: str, separator: str) -> str:
    """'1234567' -> '1,234,567' with the given separator."""
    parts = []
    while len(digits) > 3:
        parts.append(digits[-3:])
        digits = digits[:-3]
    parts.append(digits)
    return separator.join(reversed(parts))


def _plain(value: Decimal, digits: int, locale: str, *, trim: bool = False) -> str:
    """Rounded, grouped number without a sign handling surprise ('-0' never appears)."""
    rounded = _round(value, digits)
    negative = rounded < 0
    text = format(abs(rounded), "f")
    whole, _, fraction = text.partition(".")
    if trim:
        fraction = fraction.rstrip("0")
    group_sep, decimal_sep = (NNBSP, ",") if locale == "uz" else (",", ".")
    result = _group(whole, group_sep)
    if fraction:
        result += decimal_sep + fraction
    return (MINUS + result) if negative else result


def format_integer(value: object, locale: str = "uz") -> str:
    """'1 234 567' (uz) or '1,234,567' (en)."""
    _check_locale(locale)
    number = _to_decimal(value)
    return EMPTY if number is None else _plain(number, 0, locale)


def format_decimal(value: object, locale: str = "uz", digits: int = 2) -> str:
    """Fixed number of decimals: '1 234,56' (uz) or '1,234.56' (en)."""
    _check_locale(locale)
    number = _to_decimal(value)
    return EMPTY if number is None else _plain(number, digits, locale)


def format_percent(
    value: object, locale: str = "uz", digits: int = 1, *, from_ratio: bool = False
) -> str:
    """'8,2%' (uz) or '8.2%' (en). ``value`` is in percent unless ``from_ratio`` is set."""
    _check_locale(locale)
    number = _to_decimal(value)
    if number is None:
        return EMPTY
    if from_ratio:
        number *= 100
    return _plain(number, digits, locale) + "%"


def format_compact(value: object, locale: str = "uz", digits: int = 1) -> str:
    """'5 ming', '12,8 mln', '1,2 mlrd' (uz) or '5K', '12.8M', '1.2B' (en)."""
    _check_locale(locale)
    number = _to_decimal(value)
    if number is None:
        return EMPTY
    size = abs(number)
    word = letter = ""
    if _round(size, digits) < 1000:
        text = _plain(size, digits, locale, trim=True)
    else:
        index = 0
        for position, (threshold, _, _) in enumerate(COMPACT_UNITS_ASCENDING):
            if size >= threshold:
                index = position
        while True:
            threshold, word, letter = COMPACT_UNITS_ASCENDING[index]
            scaled = _round(size / threshold, digits)
            if scaled >= 1000 and index < len(COMPACT_UNITS_ASCENDING) - 1:
                index += 1  # rounding crossed into the next unit: 999.95K -> 1M
                continue
            break
        text = _plain(scaled, digits, locale, trim=True)
    sign = MINUS if number < 0 and text != "0" else ""
    if not word:
        return sign + text
    return f"{sign}{text}{NBSP}{word}" if locale == "uz" else f"{sign}{text}{letter}"


def format_currency(
    value: object,
    locale: str = "uz",
    currency: str = "UZS",
    *,
    compact: bool = True,
    digits: int | None = None,
) -> str:
    """'12,8 mln so\u02bbm' (uz) or 'UZS 12.8M' (en); full form with ``compact=False``."""
    _check_locale(locale)
    if currency not in CURRENCIES:
        raise ValueError(f"unsupported currency: {currency!r}")
    number = _to_decimal(value)
    if number is None:
        return EMPTY
    decimals, uz_symbol, en_symbol = CURRENCIES[currency]
    if compact:
        amount = format_compact(number, locale)
    else:
        amount = _plain(number, decimals if digits is None else digits, locale)
    negative = amount.startswith(MINUS)
    amount = amount.removeprefix(MINUS)
    if locale == "uz":
        result = f"{amount}{NBSP}{uz_symbol}"
    elif currency == "UZS":
        result = f"{en_symbol}{NBSP}{amount}"
    else:
        result = f"{en_symbol}{amount}"
    return (MINUS + result) if negative else result


def month_name(month: int, locale: str = "uz", *, short: bool = False) -> str:
    """Name of month 1-12. ``short`` only changes English ('Aug')."""
    _check_locale(locale)
    if not 1 <= month <= 12:
        raise ValueError("month must be 1-12")
    if locale == "en" and short:
        return MONTHS_SHORT_EN[month - 1]
    return MONTHS[locale][month - 1]


def weekday_name(weekday: int, locale: str = "uz") -> str:
    """Name of weekday 0-6 (Monday = 0, as in ``date.weekday()``)."""
    _check_locale(locale)
    if not 0 <= weekday <= 6:
        raise ValueError("weekday must be 0-6")
    return WEEKDAYS[locale][weekday]


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def format_date(value: object, locale: str = "uz", style: str = "date") -> str:
    """Date in three styles.

    * ``date``: '2026-yil 12-avgust' (uz), '12 Aug 2026' (en)
    * ``month``: '2026-yil sentabr' (uz), 'Sep 2026' (en)
    * ``short``: '12.08.2026' (uz), '2026-08-12' (en, unambiguous)
    """
    _check_locale(locale)
    day = _as_date(value)
    if day is None:
        return EMPTY
    if style == "date":
        if locale == "uz":
            return f"{day.year}-yil {day.day}-{month_name(day.month, 'uz')}"
        return f"{day.day} {month_name(day.month, 'en', short=True)} {day.year}"
    if style == "month":
        if locale == "uz":
            return f"{day.year}-yil {month_name(day.month, 'uz')}"
        return f"{month_name(day.month, 'en', short=True)} {day.year}"
    if style == "short":
        if locale == "uz":
            return f"{day.day:02d}.{day.month:02d}.{day.year}"
        return day.isoformat()
    raise ValueError(f"unknown date style: {style!r}")


# ---------------------------------------------------------------- parsing

_UNIT_FACTORS = {
    "ming": 10**3, "mln": 10**6, "mlrd": 10**9, "trln": 10**12,
    "k": 10**3, "m": 10**6, "b": 10**9, "t": 10**12,
    "тыс": 10**3, "млн": 10**6, "млрд": 10**9, "трлн": 10**12,
}
_CURRENCY_WORDS = re.compile(
    r"(so[\u02bb'\u2018\u2019`\u02bc]?m|uzs|usd|eur|rub|сум|[$\u20ac\u20bd])", re.IGNORECASE
)
_SPACES = re.compile(r"[\s\u00a0\u202f\u2009\u2007]+")
_UNIT_TAIL = re.compile(r"(ming|mlrd|mln|trln|тыс|млрд|млн|трлн|[kmbt])\.?$")


def parse_number(text: str, locale: str = "uz") -> Decimal | None:
    """Read a number written the way ``format_*`` writes it. ``None`` when it is not a number.

    Understands '1 234,56', '1,234.56', '12,8 mln', '12.8M', '8,2%', '12,8 mln soʻm',
    'UZS 12.8M' and a leading minus (also U+2212). A single ',' or '.' is ambiguous: with
    ``locale='uz'`` both are decimal marks; with ``'en'`` a ',' followed by exactly three digits
    is a group mark ('1,234') and a '.' is a decimal mark.
    """
    _check_locale(locale)
    if not isinstance(text, str):
        return None
    cleaned = _CURRENCY_WORDS.sub(" ", text.strip().lower()).replace("\u2212", "-")
    cleaned = cleaned.replace("%", " ").strip()
    factor = 1
    unit = _UNIT_TAIL.search(_SPACES.sub("", cleaned))
    if unit:
        factor = _UNIT_FACTORS[unit.group(1)]
    core = _UNIT_TAIL.sub("", _SPACES.sub("", cleaned)) if unit else _SPACES.sub("", cleaned)
    negative = core.startswith("-")
    core = core.lstrip("+-")
    if not core or not re.fullmatch(r"[\d.,]+", core) or not any(c.isdigit() for c in core):
        return None
    commas, dots = core.count(","), core.count(".")
    if commas and dots:
        decimal_mark = "," if core.rfind(",") > core.rfind(".") else "."
        group_mark = "." if decimal_mark == "," else ","
        core = core.replace(group_mark, "")
        core = core.replace(decimal_mark, ".")
    elif commas or dots:
        mark = "," if commas else "."
        count = commas or dots
        head, _, tail = core.rpartition(mark)
        if count > 1:
            if not all(len(part) == 3 for part in core.split(mark)[1:]):
                return None
            core = core.replace(mark, "")  # repeated mark: always a group mark
        elif locale == "en" and mark == "," and len(tail) == 3 and 1 <= len(head) <= 3:
            core = head + tail
        else:
            core = head + "." + tail
    try:
        value = Decimal(core) * factor
    except InvalidOperation:
        return None
    if value == 0:
        return Decimal(0)
    return -value if negative else value
