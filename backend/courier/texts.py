"""Persian text pieces shared by the courier bot, the Mini App page and the channel posts («شما» voice)."""

from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.utils import timezone

from item_requests.models import City
from miniapp.catalog import _MONTHS_FA

PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
LATIN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
ARROW = "←"

BRAND = "✈️ <b>چمدون باربری</b>"
FREE = "رایگان، بدون کمیسیون"
COST = "💬 هزینه: توافقی"

ROLE_DEMAND = "📦 بسته دارم، دنبال مسافرم"
ROLE_SUPPLY = "🧳 مسافرم، جا دارم"

# Persistent reply keyboard (5 buttons). A tap arrives as plain text.
KB_DEMAND = "📦 بسته دارم"
KB_SUPPLY = "🧳 مسافرم"
KB_ALERTS = "🔔 گوش به زنگ مسیر"
KB_MINE = "📋 درخواست‌ها و سفرهای من"
KB_HELP = "❓ راهنما و ممنوعه‌ها"
KEYBOARD_BUTTONS = (KB_DEMAND, KB_SUPPLY, KB_ALERTS, KB_MINE, KB_HELP)

BACK = "‹ برگشت"
PROHIBITED_BUTTON = "⚠️ چه چیزهایی ممنوعه؟"
NOTIFY_SUPPLY = "🔔 مسافر جدید اومد خبرم کن"
NOTIFY_DEMAND = "🔔 بسته‌ی جدید اومد خبرم کن"

DEFAULT_PROHIBITED = [
    "دارو بدون نسخه و داروهای کنترل‌شده",
    "مایعات، اسپری و ژل بیشتر از حد مجاز",
    "مواد قابل اشتعال و منفجره (فندک، گاز، باتری یدکی بزرگ)",
    "پول نقد و طلا بالای حد مجاز گمرک",
    "مواد غذایی تازه: گوشت، لبنیات، میوه و بذر",
    "سیگار و الکل بیشتر از سهمیه‌ی مسافر",
    "سلاح، مواد مخدر و هر بسته‌ای که داخلش را ندیده‌اید",
    "اقلام ممنوع گمرکی ایران، کانادا، آلمان و کشور مقصد",
]
PROHIBITED_FOOTER = "قوانین گمرک هر کشور را خودتان چک کنید. مسافر حق دارد بسته را باز کند و ببیند."
PROHIBITED_HINT = "⚠️ دارو بدون نسخه، مایعات، مواد قابل اشتعال و پول نقد بالای حد مجاز ممنوعه."

# Step 4 chips: code -> (label, package kg, free space kg, documents only).
SIZES: dict[str, tuple[str, Decimal, Decimal, bool]] = {
    "doc": ("📄 مدرک / نامه", Decimal("0.50"), Decimal("0.50"), True),
    "1": ("تا ۱ کیلو", Decimal("1.00"), Decimal("1.00"), False),
    "3": ("۱ تا ۳ کیلو", Decimal("3.00"), Decimal("3.00"), False),
    "3p": ("۳ کیلو +", Decimal("5.00"), Decimal("10.00"), False),
}
SIZE_SUPPLY_LABEL = {"doc": "📄 فقط مدرک"}
# What a package is (categories for the matcher) and what a traveler takes.
DEMAND_CATEGORIES = {"doc": ["DOCUMENTS"]}
DEMAND_PACKAGE_CATEGORIES = ["PERSONAL_ITEMS", "CLOTHES"]
SUPPLY_CATEGORIES = {"doc": ["DOCUMENTS"]}
SUPPLY_PACKAGE_CATEGORIES = ["DOCUMENTS", "CLOTHES", "PERSONAL_ITEMS", "OTHER"]


def fa(value) -> str:
    """Persian digits for numbers and dates."""
    return str(value).translate(PERSIAN_DIGITS)


def latin(text: str) -> str:
    return (text or "").translate(LATIN_DIGITS)


def esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


def month_name(value: date) -> str:
    return _MONTHS_FA[value.month - 1]


def day_month(value: date | None) -> str:
    if value is None:
        return ""
    return f"{fa(value.day)} {month_name(value)}"


def kg_text(value) -> str:
    if value is None:
        return ""
    amount = float(value)
    text = str(int(amount)) if amount.is_integer() else f"{amount:.2f}".rstrip("0").rstrip(".")
    return f"{fa(text)} کیلو"


def size_label(code: str, role: str) -> str:
    if role == "SUPPLY" and code in SIZE_SUPPLY_LABEL:
        return SIZE_SUPPLY_LABEL[code]
    return SIZES[code][0] if code in SIZES else ""


def city_name(country: str, slug: str) -> str:
    name = (
        City.objects.filter(country__code=(country or "").upper(), slug=slug)
        .values_list("name_fa", flat=True)
        .first()
    )
    return name or (slug or "").replace("-", " ").title()


def route_text(origin_country: str, origin_city: str, stops: list[tuple[str, str]]) -> str:
    origin = city_name(origin_country, origin_city)
    names = " و ".join(city_name(country, city) for country, city in stops)
    return f"{origin} {ARROW} {names}"


def request_route(item) -> str:
    return route_text(item.origin_country, item.origin_city, item.destination_stop_pairs())


def hashtag_word(name: str) -> str:
    """A city name as one hashtag word: spaces, ZWNJ and dashes become underscores."""
    word = re.sub(r"[\s‌\-.]+", "_", (name or "").strip())
    return re.sub(r"[^\w]", "", word).strip("_")


def relative_day(value: datetime | None) -> str:
    if value is None:
        return ""
    days = (timezone.localdate() - timezone.localtime(value).date()).days
    if days <= 0:
        return "امروز ثبت شد"
    if days == 1:
        return "دیروز ثبت شد"
    return f"{fa(days)} روز پیش"


def month_end(year: int, month: int) -> date:
    first_next = date(year + (month // 12), month % 12 + 1, 1)
    return first_next - timedelta(days=1)


def upcoming_months(today: date | None = None, count: int = 3) -> list[tuple[str, date, date]]:
    """(key YYYY-MM, first usable day, last day) for this month and the next ones."""
    today = today or timezone.localdate()
    found = []
    year, month = today.year, today.month
    for _ in range(count):
        start = today if (year, month) == (today.year, today.month) else date(year, month, 1)
        found.append((f"{year:04d}-{month:02d}", start, month_end(year, month)))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return found


def parse_typed_date(text: str, today: date | None = None) -> date | None:
    """2026-12-12, 2026/12/12, 12/12 or «12 دسامبر» (Persian or Latin digits). Past dates are refused."""
    today = today or timezone.localdate()
    raw = latin(text).strip()
    found: date | None = None
    full = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", raw)
    short = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})", raw)
    try:
        if full:
            found = date(int(full.group(1)), int(full.group(2)), int(full.group(3)))
        elif short:
            day, month = int(short.group(1)), int(short.group(2))
            found = date(today.year, month, day)
            if found < today:
                found = date(today.year + 1, month, day)
        else:
            named = re.fullmatch(r"(\d{1,2})\s*(\S+)", raw)
            if named and named.group(2) in _MONTHS_FA:
                month = _MONTHS_FA.index(named.group(2)) + 1
                found = date(today.year, month, int(named.group(1)))
                if found < today:
                    found = date(today.year + 1, month, int(named.group(1)))
    except ValueError:
        return None
    if found is None or found < today or (found - today).days > 365:
        return None
    return found


def prohibited_items() -> list[str]:
    from courier import conf

    custom = [line.strip(" •-\t") for line in (conf.row().prohibited_text or "").splitlines()]
    custom = [line for line in custom if line]
    return custom or list(DEFAULT_PROHIBITED)


def prohibited_text() -> str:
    lines = ["⚠️ <b>چه چیزهایی ممنوعه؟</b>", "این‌ها را با مسافر نفرستید و قبول نکنید:"]
    lines.extend(f"• {esc(item)}" for item in prohibited_items())
    lines.extend(["", f"<i>{PROHIBITED_FOOTER}</i>"])
    return "\n".join(lines)
