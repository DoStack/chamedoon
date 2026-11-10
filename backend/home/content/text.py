"""Words of the content posts: Persian digits and money, dates, captions, voice scripts and start params."""

from __future__ import annotations

import html
import re
from datetime import date

import jdatetime

from home import deeplink
from home.publish import CITY_FA, FURNISHED_FA, MONTHS, place_fa, rent_basis

BOT = "chamedoon_home_bot"
TYPE_FA = {"room": "اتاق", "basement": "بیسمنت", "studio": "استودیو", "apartment": "آپارتمان", "house": "خانه", "other": "سایر"}
SPOKEN_TYPE = {"room": "اتاق", "basement": "بیسمنت", "studio": "استودیو", "apartment": "آپارتمان", "house": "خونه", "other": "واحد"}
SYMBOL = {"CAD": "$", "EUR": "€"}
CURRENCY_FA = {"CAD": "دلار", "EUR": "یورو"}
JALALI_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
WEEKDAYS = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]
_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa(value) -> str:
    return str(value).translate(_DIGITS)


def money(value: float) -> str:
    """Whole amount with «٬» between thousands, Persian digits: 1600 -> ۱٬۶۰۰."""
    return fa(f"{int(round(value)):,}").replace(",", "٬")


def city_fa(city: str) -> str:
    return CITY_FA.get((city or "").casefold(), city)


def jalali(day: date) -> str:
    """«۲۰ آبان»."""
    j = jdatetime.date.fromgregorian(date=day)
    return f"{fa(j.day)} {JALALI_MONTHS[j.month - 1]}"


def dates_line(day: date) -> str:
    """Shamsi and Gregorian: «سه‌شنبه ۲۰ آبان ۱۴۰۵ · ۱۱ نوامبر ۲۰۲۶»."""
    j = jdatetime.date.fromgregorian(date=day)
    return f"{WEEKDAYS[day.weekday()]} {jalali(day)} {fa(j.year)} · {fa(day.day)} {MONTHS[day.month - 1]} {fa(day.year)}"


_ONES = ["", "یک", "دو", "سه", "چهار", "پنج", "شش", "هفت", "هشت", "نه"]
_TEENS = ["ده", "یازده", "دوازده", "سیزده", "چهارده", "پونزده", "شونزده", "هفده", "هجده", "نوزده"]
_TENS = ["", "", "بیست", "سی", "چهل", "پنجاه", "شصت", "هفتاد", "هشتاد", "نود"]
_HUNDREDS = ["", "صد", "دویست", "سیصد", "چهارصد", "پونصد", "ششصد", "هفتصد", "هشتصد", "نهصد"]


def _below_thousand(n: int) -> list[str]:
    parts = []
    if n >= 100:
        parts.append(_HUNDREDS[n // 100])
        n %= 100
    if 10 <= n < 20:
        parts.append(_TEENS[n - 10])
    else:
        if n >= 20:
            parts.append(_TENS[n // 10])
        if n % 10:
            parts.append(_ONES[n % 10])
    return parts


def words(n: int) -> str:
    """A whole number as spoken Persian (colloquial where TTS reads it better): 1900 -> «هزار و نهصد»."""
    n = int(round(n))
    if n == 0:
        return "صفر"
    parts = []
    if n >= 1000:
        thousands = n // 1000
        parts.append("هزار" if thousands == 1 else " و ".join(_below_thousand(thousands)) + " هزار")
        n %= 1000
    parts += _below_thousand(n)
    return " و ".join(parts)


def link(param: str) -> str:
    return f"https://t.me/{BOT}?startapp={param}"


def city_slug(city: str) -> str:
    return deeplink.slug(city)


def daily_params(city: str, day: date, deal_id: int | None) -> dict[str, str]:
    """§6 start params of a daily post."""
    base = f"dg_{city_slug(city)}_{day:%Y%m%d}"
    return {
        "all": base,
        "deal": f"deal_{city_slug(city)}_{deal_id}" if deal_id else base,
        "alert": f"{base}_alert",
        "post": f"{base}_post",
    }


def deal_title(listing) -> str:
    """Type, bedrooms, size, furnished, then the area in Persian: «اتاق مبله در اسکاربرو»."""
    from home.publish import title

    text = title(listing)
    area = (listing.home_area.fa if listing.home_area_id else "") or place_fa((listing.area or "").split(",")[0].strip())
    if area and area.casefold() != (listing.city or "").casefold() and len(area) <= 30 and not re.search(r"\d", area):
        text += f" در {area}"
    return text


def spoken_place(listing) -> str:
    """« توی اسکاربرو» when the area has a Persian name, else blank (TTS mangles Latin names)."""
    area = (listing.home_area.fa if listing.home_area_id else "") or place_fa((listing.area or "").split(",")[0].strip())
    return f" توی {area}" if area and re.search(r"[؀-ۿ]", area) and len(area) <= 24 else ""


def spoken_type(listing, kind: str) -> str:
    word = SPOKEN_TYPE.get(kind, "واحد")
    if kind in {"apartment", "house", "basement"} and listing.rooms and listing.country != "DE":
        word += f"ِ {words(int(listing.rooms))} خوابه"
    elif listing.furnished in FURNISHED_FA:
        word += f"ِ {FURNISHED_FA[listing.furnished]}"
    return word


def basis_fa(listing) -> str:
    return rent_basis(listing) if listing.country == "DE" else ""


def esc(value) -> str:
    return html.escape(str(value or ""), quote=False)
