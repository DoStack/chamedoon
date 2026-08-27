from __future__ import annotations

from datetime import date
from urllib.parse import quote

from item_requests.models import Category, City
from matching.models import Match, MatchStatus
from users.models import User

CATEGORY_EMOJI = {
    "DOCUMENTS": "📄",
    "CLOTHES": "👕",
    "PERSONAL_ITEMS": "🧳",
    "ELECTRONICS": "📱",
    "FOOD": "🍱",
    "MEDICINE": "💊",
    "CIGARETTES": "🚬",
    "FRAGILE": "⚠️",
    "OTHER": "📦",
}

MAX_TELEGRAM_BUTTON_URL = 2048


def category_line(category: Category) -> str:
    emoji = CATEGORY_EMOJI.get(category.code, "📦")
    return f"{emoji} {category.name_fa}"


def telegram_dm_contact(user: User, draft: str = "") -> dict:
    username = (user.telegram_username or "").strip() or None
    telegram_user_id = int(user.telegram_user_id)
    https_url = f"https://t.me/{username}" if username else None
    if https_url and draft:
        with_text = f"{https_url}?text={quote(draft, safe='')}"
        if len(with_text) <= MAX_TELEGRAM_BUTTON_URL:
            https_url = with_text
    return {
        "first_name": user.first_name,
        "telegram_username": username,
        "telegram_user_id": telegram_user_id,
        "https_url": https_url,
        "telegram_url": https_url or f"tg://user?id={telegram_user_id}",
        "draft": draft,
    }


def intro_draft_for_match(match: Match, user: User) -> str:
    other_request = match.counterpart_request(user)
    role = match.role_for(user)
    if other_request is None or role is None:
        return ""
    name = (other_request.user.first_name or "").strip() or "دوست"
    if role == "demand":
        return _demand_draft(name, match.demand_request)
    return _supply_draft(name, match.demand_request, match.supply_request)


def contact_for_match(match: Match, user: User) -> dict | None:
    if match.status not in {MatchStatus.CONNECTED, MatchStatus.COMPLETED}:
        return None
    other_request = match.counterpart_request(user)
    if other_request is None:
        return None
    return telegram_dm_contact(other_request.user, intro_draft_for_match(match, user))


def _demand_draft(name: str, demand) -> str:
    categories = _category_block(demand.item_categories.order_by("sort_order"))
    return "\n".join(
        [
            f"سلام {name} 👋",
            "از کولبر به شما پیام میدم.",
            "",
            "من یه بسته دارم و می‌خوام",
            _route_line(demand),
            "بفرستم.",
            "",
            "📦 نوع بسته:",
            categories or "📦 سایر",
            "",
            "آیا ظرفیت دارید که برای هماهنگی زمان و محل تحویل، همین‌جا با هم هماهنگ شیم لطفا؟ 🙏",
        ]
    )


def _supply_draft(name: str, demand, supply) -> str:
    allowed = _category_block(supply.item_categories.order_by("sort_order"))
    excluded = _category_block(supply.excluded_categories.order_by("sort_order"))
    extra = (supply.excluded_other_text or "").strip()
    lines = [
        f"سلام {name} 👋",
        "از کولبر به شما پیام میدم.",
        "",
        "من قراره",
        _route_line(supply),
        "سفر کنم و ظرفیت برای حمل موارد زیر دارم:",
        "",
        allowed or "📦 سایر",
    ]
    if excluded or extra:
        lines.extend(["", "❌ این موارد رو هم حمل نمی‌کنم: ❌"])
        if excluded:
            lines.append(excluded)
        if extra:
            lines.append(f"📦 {extra}")
    lines.extend(
        [
            "",
            f"📅 تاریخ پرواز: {_day_month(supply.date_from, supply.date_to)}",
            f"📅 تاریخ دریافت: {_day_month(demand.date_from, demand.date_to)}",
            "",
            "اگر بسته‌ای برای این مسیر دارید، می‌تونیم برای هماهنگی جزئیات، زمان و محل تحویل همین‌جا با هم هماهنگ شیم. 🙏",
        ]
    )
    return "\n".join(lines)


def _category_block(categories) -> str:
    return "\n".join(category_line(category) for category in categories)


def _route_line(item_request) -> str:
    origin = _flagged_city(item_request.origin_country, item_request.origin_city)
    destination = _flagged_city(item_request.destination_country, item_request.destination_city)
    return f"از {origin} به {destination}"


def _flagged_city(country_code: str, slug: str) -> str:
    flag = _country_flag(country_code)
    name = _city_fa(country_code, slug)
    return f"{flag} {name}".strip() if flag else name


def _country_flag(code: str) -> str:
    letters = (code or "").upper()
    if len(letters) != 2 or not letters.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(letter) - ord("A")) for letter in letters)


def _day_month(start: date, end: date) -> str:
    if start == end:
        return _format_day_month(start)
    return f"از {_format_day_month(start)} تا {_format_day_month(end)}"


def _format_day_month(value: date) -> str:
    return f"{value.day} {value.strftime('%B')}"


def _city_fa(country_code: str, slug: str) -> str:
    name = (
        City.objects.filter(country__code=country_code, slug=slug, is_active=True)
        .values_list("name_fa", flat=True)
        .first()
    )
    return name or slug
