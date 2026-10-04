from __future__ import annotations

import random
from datetime import datetime

from django.utils import timezone

from item_requests.models import ItemRequest
from market.migrate import DEFAULT_AUTHOR_FIRST_NAME
from matching.contact import city_fa, clean_telegram_username
from matching.models import Match
from notifications.telegram import mini_app_in_chat_link
from outreach.persian import fa_date, fa_date_range, fa_digits, tehran_date

COUNT_WORDS = {1: "یه", 2: "دو تا", 3: "سه تا", 4: "چهار تا", 5: "پنج تا"}

# Each message picks one line per slot, seeded by its token, so it reads like a
# person typed it and re-rendering the same message gives the same wording.
HELLO = ("سلام{name}، وقتتون بخیر 🙂", "سلام{name}، وقت بخیر", "سلام{name} 🙂")
SAW_POST = (
    "دیدم {when} پست گذاشته بودید که دنبال کسی هستید بارتون رو {route}.",
    "{when} پستتون رو دیدم که دنبال یکی می‌گشتید بارتون رو {route}.",
)
ONE_TRAVELER = (
    "من از چمدونم؛ یه مسافر پیدا کردیم که {date} همین مسیر رو می‌ره.",
    "ما توی چمدون یه مسافر برای همین مسیر پیدا کردیم که {date} می‌ره.",
)
MANY_TRAVELERS = (
    "من از چمدونم؛ {count} مسافر پیدا کردیم که همین مسیر رو می‌رن، اولیش {date}.",
    "ما توی چمدون {count} مسافر برای همین مسیر پیدا کردیم، اولیش {date} می‌ره.",
)
LINK_ONE = (
    "اگه هنوز لازم دارید، مشخصاتش اینجاست و می‌تونید مستقیم بهش پیام بدید:",
    "اگه هنوز دنبالشید، از این لینک می‌تونید ببینیدش و مستقیم باهاش حرف بزنید:",
)
LINK_MANY = (
    "اگه هنوز لازم دارید، مشخصاتشون اینجاست و می‌تونید مستقیم بهشون پیام بدید:",
    "اگه هنوز دنبالشید، از این لینک می‌تونید ببینیدشون و مستقیم باهاشون حرف بزنید:",
)
LINK_LABEL_ONE = "دیدن مشخصات مسافر"
LINK_LABEL_MANY = "دیدن مسافرها"
# The operator answers replies personally from this account, so the DM invites questions.
GOODBYE = (
    "هر سوالی داشتید همین‌جا بپرسید 🙏",
    "اگه سوالی بود در خدمتم 🙂",
)


def outreach_link(token: str) -> str:
    # Main Mini App link, like channel posts: the bots have no Mini App with the short name "app".
    return mini_app_in_chat_link(f"o_{token}")


def outreach_text(
    demand: ItemRequest,
    matches: list[Match],
    token: str,
    *,
    now: datetime | None = None,
) -> str:
    rng = random.Random(token)
    link = outreach_link(token)
    name = greeting_name(demand.user)
    supplies = [match.supply_request for match in matches]
    first = min(supplies, key=lambda supply: supply.flight_date or supply.date_from)
    # Count people, not trips: one traveler can list several trips on the same route.
    travelers = len({supply.user_id for supply in supplies})
    if travelers == 1:
        offer = rng.choice(ONE_TRAVELER).format(date=travel_when(first))
        link_line = rng.choice(LINK_ONE)
        label = LINK_LABEL_ONE
    else:
        count = COUNT_WORDS.get(travelers, f"{fa_digits(travelers)} تا")
        offer = rng.choice(MANY_TRAVELERS).format(count=count, date=travel_when(first))
        link_line = rng.choice(LINK_MANY)
        label = LINK_LABEL_MANY
    lines = [
        rng.choice(HELLO).format(name=f" {name}" if name else ""),
        rng.choice(SAW_POST).format(when=posted_ago(demand, now or timezone.now()), route=route_phrase(demand)),
        offer,
        link_line,
        # Markdown links: the worker sends with Telethon's markdown parse mode, which keeps the URL
        # intact inside [..](..). The label is tappable on mobile; the visible URL below it is for
        # Telegram Desktop, which shows links from unknown senders as plain text.
        f"👈 [{label}]({link})",
        f"[{link}]({link})",
        rng.choice(GOODBYE),
    ]
    return "\n".join(lines)


def greeting_name(user) -> str:
    name = (user.first_name or "").strip()
    if not name or name == DEFAULT_AUTHOR_FIRST_NAME:
        return ""
    handle = clean_telegram_username(name)
    if handle and handle.lower() == (user.telegram_username or "").lower():
        return ""
    return name[:32]


def route_phrase(demand: ItemRequest) -> str:
    """Spoken order: «از تهران ببره میلان»."""
    origin = city_fa(demand.origin_country, demand.origin_city)
    stops = [city_fa(country, city) for country, city in demand.destination_stop_pairs()]
    destination = " یا ".join(stops) or city_fa(demand.destination_country, demand.destination_city)
    return f"از {origin} ببره {destination}"


def posted_ago(demand: ItemRequest, now: datetime) -> str:
    days = (tehran_date(now) - tehran_date(posted_at_for(demand))).days
    if days <= 0:
        return "امروز"
    if days == 1:
        return "دیروز"
    return "چند روز پیش"


def travel_when(supply: ItemRequest) -> str:
    if supply.flight_date:
        return fa_date(supply.flight_date)
    if supply.date_from == supply.date_to:
        return fa_date(supply.date_from)
    return f"بین {fa_date_range(supply.date_from, supply.date_to)}"


def posted_at_for(demand: ItemRequest) -> datetime:
    post = market_post_for(demand)
    return post.posted_at if post is not None else demand.created_at


def market_post_for(demand: ItemRequest):
    from market.models import MarketPost

    try:
        return demand.market_post
    except MarketPost.DoesNotExist:
        return None
