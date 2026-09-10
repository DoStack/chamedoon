from __future__ import annotations

import re
from datetime import date, datetime, timedelta

_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

_MONTHS = {
    "jan": 1,
    "january": 1,
    "ژانویه": 1,
    "feb": 2,
    "february": 2,
    "فوریه": 2,
    "mar": 3,
    "march": 3,
    "مارس": 3,
    "apr": 4,
    "april": 4,
    "آوریل": 4,
    "may": 5,
    "مه": 5,
    "jun": 6,
    "june": 6,
    "ژوئن": 6,
    "جون": 6,
    "jul": 7,
    "july": 7,
    "جولای": 7,
    "ژوئیه": 7,
    "ژولای": 7,
    "aug": 8,
    "august": 8,
    "اوت": 8,
    "آگوست": 8,
    "اگوست": 8,
    "اگست": 8,
    "آگست": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "سپتامبر": 9,
    "oct": 10,
    "october": 10,
    "اکتبر": 10,
    "nov": 11,
    "november": 11,
    "نوامبر": 11,
    "dec": 12,
    "december": 12,
    "دسامبر": 12,
}

_MONTH_PATTERN = "|".join(sorted((_MONTHS), key=len, reverse=True))
_DAY_MONTH = re.compile(
    rf"(?<!\d)(\d{{1,2}})\s*(?:ام)?\s*({_MONTH_PATTERN})\s*(\d{{4}})?",
    re.I,
)
_MONTH_DAY = re.compile(
    rf"({_MONTH_PATTERN})\s+(\d{{1,2}})(?:\s*,?\s*(\d{{4}}))?",
    re.I,
)
_ISO = re.compile(r"(20\d{2})-(\d{1,2})-(\d{1,2})")

_ORDINAL_DAYS = {
    "سی و یکم": 31,
    "سی‌ام": 30,
    "سی ام": 30,
    "بیست و نهم": 29,
    "بیست و هشتم": 28,
    "بیست و هفتم": 27,
    "بیست و ششم": 26,
    "بیست و پنجم": 25,
    "بیست و چهارم": 24,
    "بیست و سوم": 23,
    "بیست و دوم": 22,
    "بیست و یکم": 21,
    "بیست ویکم": 21,
    "بیستم": 20,
    "نوزدهم": 19,
    "هیجدهم": 18,
    "هجدهم": 18,
    "هفدهم": 17,
    "شانزدهم": 16,
    "پانزدهم": 15,
    "چهاردهم": 14,
    "سیزدهم": 13,
    "دوازدهم": 12,
    "یازدهم": 11,
    "دهم": 10,
    "نهم": 9,
    "هشتم": 8,
    "هفتم": 7,
    "ششم": 6,
    "پنجم": 5,
    "چهارم": 4,
    "سوم": 3,
    "دوم": 2,
    "یکم": 1,
    "اول": 1,
}
_ORDINAL_PATTERN = "|".join(sorted(_ORDINAL_DAYS, key=len, reverse=True))
_ORDINAL_MONTH = re.compile(
    rf"({_ORDINAL_PATTERN})\s*(?:ام)?\s*({_MONTH_PATTERN})\s*(\d{{4}})?",
    re.I,
)

DEFAULT_OFFSET_DAYS = 14
LEAD_DAYS_BEFORE_FLIGHT = 3


def _normalize(text: str) -> str:
    return text.translate(_PERSIAN_DIGITS).replace("‌", " ")


def parse_travel_date(text: str, *, posted_at: datetime) -> date | None:
    haystack = _normalize(text)
    posted = posted_at.date()
    for match in _ISO.finditer(haystack):
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            continue
    for match in _DAY_MONTH.finditer(haystack):
        parsed = _build_date(int(match.group(1)), match.group(2), match.group(3), posted)
        if parsed:
            return parsed
    for match in _MONTH_DAY.finditer(haystack):
        parsed = _build_date(int(match.group(2)), match.group(1), match.group(3), posted)
        if parsed:
            return parsed
    for match in _ORDINAL_MONTH.finditer(haystack):
        day = _ORDINAL_DAYS.get(match.group(1).replace("‌", " ").strip())
        if not day:
            continue
        parsed = _build_date(day, match.group(2), match.group(3), posted)
        if parsed:
            return parsed
    return None


def travel_date_for_post(text: str, *, posted_at: datetime, today: date) -> date | None:
    parsed = parse_travel_date(text, posted_at=posted_at)
    if parsed is None:
        return today + timedelta(days=DEFAULT_OFFSET_DAYS)
    if parsed < today:
        return None
    return parsed


def supply_travel_window(
    text: str,
    *,
    posted_at: datetime,
    today: date,
    hinted_flight: date | None = None,
) -> dict[str, date] | None:
    parsed = parse_travel_date(text, posted_at=posted_at)
    if parsed is not None and parsed < today:
        return None
    flight = parsed or hinted_flight
    if flight is None:
        flight = today + timedelta(days=DEFAULT_OFFSET_DAYS)
    if flight < today:
        return None
    posted = posted_at.date()
    date_from = posted if posted <= flight else today
    if date_from > flight:
        date_from = flight
    date_to = flight - timedelta(days=LEAD_DAYS_BEFORE_FLIGHT)
    if date_to < date_from:
        date_to = date_from
    return {"flight_date": flight, "date_from": date_from, "date_to": date_to}


def _build_date(day: int, month_token: str, year_raw: str | None, posted: date) -> date | None:
    month = _MONTHS.get(month_token.lower())
    if not month:
        return None
    year = int(year_raw) if year_raw else posted.year
    try:
        value = date(year, month, day)
    except ValueError:
        return None
    if year_raw is None and value < posted - timedelta(days=60):
        try:
            value = date(year + 1, month, day)
        except ValueError:
            return value
    return value
