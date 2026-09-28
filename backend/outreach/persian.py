from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import jdatetime

# Iran dropped daylight saving in 2022, so a fixed offset is exact and needs no tzdata.
TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30), "IRST")

FA_MONTHS = (
    "فروردین",
    "اردیبهشت",
    "خرداد",
    "تیر",
    "مرداد",
    "شهریور",
    "مهر",
    "آبان",
    "آذر",
    "دی",
    "بهمن",
    "اسفند",
)
_FA_DIGITS = str.maketrans("0123456789.", "۰۱۲۳۴۵۶۷۸۹٫")


def fa_digits(value) -> str:
    return str(value).translate(_FA_DIGITS)


def tehran_date(value: datetime) -> date:
    return value.astimezone(TEHRAN_TZ).date()


def fa_date(value: date) -> str:
    """Jalali day and month, e.g. ۱۵ مهر."""
    jalali = jdatetime.date.fromgregorian(date=value)
    return f"{fa_digits(jalali.day)} {FA_MONTHS[jalali.month - 1]}"


def fa_date_range(start: date, end: date) -> str:
    if start == end:
        return fa_date(start)
    first = jdatetime.date.fromgregorian(date=start)
    last = jdatetime.date.fromgregorian(date=end)
    if (first.year, first.month) == (last.year, last.month):
        return f"{fa_digits(first.day)} تا {fa_date(end)}"
    return f"{fa_date(start)} تا {fa_date(end)}"


def fa_kg(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return fa_digits(text)
