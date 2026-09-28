from __future__ import annotations

from item_requests.models import ItemRequest
from market.migrate import DEFAULT_AUTHOR_FIRST_NAME
from matching.contact import clean_telegram_username, route_line
from matching.models import Match
from notifications.telegram import mini_app_start_link
from outreach.persian import fa_date, fa_date_range, fa_digits, fa_kg, tehran_date

OPT_OUT_WORD = "لغو"
MAX_TRAVELERS_LISTED = 3


def outreach_link(token: str) -> str:
    return mini_app_start_link(f"o_{token}")


def outreach_text(demand: ItemRequest, matches: list[Match], token: str) -> str:
    name = greeting_name(demand.user)
    lines = [
        f"سلام {name} 👋" if name else "سلام 👋",
        "از «چمدون» پیام میدم.",
        "",
        f"{source_phrase(demand)} نوشته بودید می‌خواید بار {route_line(demand)} بفرستید.",
        "",
        f"✈️ {fa_digits(len(matches))} مسافر برای همین مسیر پیدا کردیم:",
    ]
    lines.extend(f"• {traveler_line(match.supply_request)}" for match in matches[:MAX_TRAVELERS_LISTED])
    if len(matches) > MAX_TRAVELERS_LISTED:
        lines.append(f"• و {fa_digits(len(matches) - MAX_TRAVELERS_LISTED)} مسافر دیگه")
    lines.extend(
        [
            "",
            "برای دیدن مسافرها و پیام دادن مستقیم بهشون:",
            f"👈 {outreach_link(token)}",
            "",
            f"اگه بارتون رو فرستادید یا نمی‌خواید دیگه پیام بدیم، فقط بنویسید «{OPT_OUT_WORD}» 🙏",
        ]
    )
    return "\n".join(lines)


def greeting_name(user) -> str:
    name = (user.first_name or "").strip()
    if not name or name == DEFAULT_AUTHOR_FIRST_NAME:
        return ""
    handle = clean_telegram_username(name)
    if handle and handle.lower() == (user.telegram_username or "").lower():
        return ""
    return name[:32]


def source_phrase(demand: ItemRequest) -> str:
    post = market_post_for(demand)
    if post is None:
        return "توی تلگرام"
    when = fa_date(tehran_date(post.posted_at))
    channel = (post.channel_username or "").strip().lstrip("@")
    if channel:
        return f"{when} توی کانال «{channel}»"
    return f"{when} توی تلگرام"


def traveler_line(supply: ItemRequest) -> str:
    if supply.flight_date:
        when = fa_date(supply.flight_date)
    else:
        when = fa_date_range(supply.date_from, supply.date_to)
    if supply.capacity_kg:
        return f"{when} — تا {fa_kg(supply.capacity_kg)} کیلو"
    return when


def market_post_for(demand: ItemRequest):
    from market.models import MarketPost

    try:
        return demand.market_post
    except MarketPost.DoesNotExist:
        return None
