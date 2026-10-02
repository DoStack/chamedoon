from __future__ import annotations

import random
from datetime import datetime

from django.utils import timezone

from item_requests.models import ItemRequest
from market.migrate import DEFAULT_AUTHOR_FIRST_NAME
from matching.contact import city_fa, clean_telegram_username
from matching.models import Match
from notifications.telegram import mini_app_start_link
from outreach.persian import fa_date, fa_date_range, fa_digits, tehran_date

OPT_OUT_WORD = "لغو"
COUNT_WORDS = {1: "یه", 2: "دو تا", 3: "سه تا", 4: "چهار تا", 5: "پنج تا"}

# Each message picks one line per slot, seeded by its token, so it reads like a
# person typed it and re-rendering the same message gives the same wording.
HELLO = ("سلام{name}، وقتتون بخیر 🙂", "سلام{name}، وقت بخیر", "سلام{name} 🙂")
SAW_POST = (
    "دیدم {when} پست گذاشته بودید که دنبال کسی هستید بارتون رو {route}.",
    "{when} پستتون رو دیدم که دنبال یکی می‌گشتید بارتون رو {route}.",
)
ONE_TRAVELER = (
    "من از چمدونم؛ یه مسافر داریم که {date} همین مسیر رو می‌ره.",
    "ما توی چمدون یه مسافر برای همین مسیر داریم که {date} می‌ره.",
)
MANY_TRAVELERS = (
    "من از چمدونم؛ {count} مسافر داریم که همین مسیر رو می‌رن، اولیش {date}.",
    "ما توی چمدون {count} مسافر برای همین مسیر داریم، اولیش {date} می‌ره.",
)
LINK_ONE = (
    "اگه هنوز لازم دارید، مشخصاتش اینجاست و می‌تونید مستقیم بهش پیام بدید:",
    "اگه هنوز دنبالشید، از این لینک می‌تونید ببینیدش و مستقیم باهاش حرف بزنید:",
)
LINK_MANY = (
    "اگه هنوز لازم دارید، مشخصاتشون اینجاست و می‌تونید مستقیم بهشون پیام بدید:",
    "اگه هنوز دنبالشید، از این لینک می‌تونید ببینیدشون و مستقیم باهاشون حرف بزنید:",
)
GOODBYE = (
    f"اگه بارتون رفته یا دیگه لازم نیست، فقط بنویسید «{OPT_OUT_WORD}» که دیگه مزاحمتون نشم 🙏",
    f"اگه دیگه لازم نیست، «{OPT_OUT_WORD}» رو بفرستید که دیگه پیام ندم 🙏",
)


def outreach_link(token: str) -> str:
    return mini_app_start_link(f"o_{token}")


def outreach_text(
    demand: ItemRequest,
    matches: list[Match],
    token: str,
    *,
    now: datetime | None = None,
) -> str:
    rng = random.Random(token)
    name = greeting_name(demand.user)
    supplies = [match.supply_request for match in matches]
    first = min(supplies, key=lambda supply: supply.flight_date or supply.date_from)
    if len(supplies) == 1:
        offer = rng.choice(ONE_TRAVELER).format(date=travel_when(first))
        link_line = rng.choice(LINK_ONE)
    else:
        count = COUNT_WORDS.get(len(supplies), f"{fa_digits(len(supplies))} تا")
        offer = rng.choice(MANY_TRAVELERS).format(count=count, date=travel_when(first))
        link_line = rng.choice(LINK_MANY)
    lines = [
        rng.choice(HELLO).format(name=f" {name}" if name else ""),
        rng.choice(SAW_POST).format(when=posted_ago(demand, now or timezone.now()), route=route_phrase(demand)),
        offer,
        link_line,
        outreach_link(token),
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
