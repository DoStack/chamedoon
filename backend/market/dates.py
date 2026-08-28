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

DEFAULT_OFFSET_DAYS = 14


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
    return None


def travel_date_for_post(text: str, *, posted_at: datetime, today: date) -> date | None:
    parsed = parse_travel_date(text, posted_at=posted_at)
    if parsed is None:
        return posted_at.date() + timedelta(days=DEFAULT_OFFSET_DAYS)
    if parsed < today:
        return None
    return parsed


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
