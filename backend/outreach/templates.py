"""The first messages and the follow-ups, word for word as the proposal has them.

Each message picks its wording by its token, so a re-render reads the same and the funnel can compare wordings.
No message carries an exit line and none has a button: the agent reads the answer (decisions 2 and 3).
Links are written `[url](url)`: the worker sends with Telethon's markdown, and Telegram Desktop shows the URL.
"""

from __future__ import annotations

import random
import re
from decimal import Decimal

from home.alerts import city_fa

PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
INTRO = "از تیم چمدون پیام می‌دم خدمتتون."
HELLO_NAMED = ("سلام {name} جان، وقت بخیر.",)
HELLO_PLAIN = ("سلام وقت بخیر.", "سلام علیکم، وقت بخیر.")
CURRENCY_FA = {"CAD": "دلار", "USD": "دلار", "EUR": "یورو"}

# A name is used only when it looks like a person's first name.
_NAME = re.compile(r"^[A-Za-zء-يپچژکگی‌]{2,18}$")
NOT_A_NAME = {
    "admin", "agent", "amlak", "apartment", "bot", "broker", "chamedoon", "group", "home", "homes", "house", "housing",
    "immobilien", "info", "realestate", "realtor", "rent", "room", "sale", "shop", "support", "team", "user", "test",
    "املاک", "مشاور", "مشاوره", "گروه", "کانال", "ادمین", "خانه", "خونه", "اجاره", "فروش", "مسکن", "شرکت", "چمدون", "چمدان", "پشتیبانی", "کاربر",
}


def fa_number(value) -> str:
    if isinstance(value, Decimal):
        value = int(value) if value == value.to_integral_value() else value
    return str(value).translate(PERSIAN_DIGITS)


# Words people use as Telegram names that are not first names (audit 2026-11-08, rule 19).
NOT_A_FIRST_NAME = {
    "legend", "just", "myprofile", "profile", "my", "me", "mr", "mrs", "miss", "king", "queen", "boss", "angel", "star",
    "love", "life", "happy", "lucky", "smile", "dream", "sky", "sun", "moon", "hello", "hi", "official", "real", "the",
    "new", "best", "good", "black", "white", "dark", "light", "unknown", "account", "mac", "mir",
    "green", "blue", "red", "pink", "gold", "silver", "gray", "grey", "orange", "purple", "rose", "ocean", "rain",
    "admin", "user", "guest", "home", "house", "rent", "info", "support", "shop", "store", "hey", "mister", "lady",
}
LATIN_NAME = re.compile(r"^[A-Z][a-z]{2,10}$")  # 12+ letters is usually first and last name joined (audit 2026-11-08, #109)


def valid_name(display_name: str, username: str = "") -> str:
    """The first word of a display name when it reads as a person's first name, else nothing (then «سلام وقت بخیر،»).

    Latin names need three letters or more, a capital first letter and no other capitals, and not a common word:
    «Legend», «Just», «Ma», «Mo», «HJ», «Myprofile» are not names."""
    words = (display_name or "").strip().split()
    if not words:
        return ""
    first = words[0].strip(".,-_")
    if not _NAME.fullmatch(first) or first.casefold() in NOT_A_NAME or first.casefold() in NOT_A_FIRST_NAME:
        return ""
    if re.match(r"[A-Za-z]", first) and (not LATIN_NAME.fullmatch(first) or first.casefold().startswith("my")):
        return ""
    if username and first.casefold() == username.strip().lstrip("@").casefold():
        return ""
    if any(word.casefold() in NOT_A_NAME for word in words):
        return ""
    return first


# No emoji in anything sent to a person (Mohammad, 2026-11-06).
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]")


def no_emoji(text: str) -> str:
    cleaned = re.sub(r"[ \t]{2,}", " ", _EMOJI.sub("", text or ""))
    return re.sub(r"[ \t]+(\n|$)", r"\1", cleaned).strip()


def greeting(name: str, rng: random.Random) -> str:
    return rng.choice(HELLO_NAMED).format(name=name) if name else rng.choice(HELLO_PLAIN)


def opening(name: str, rng: random.Random) -> str:
    return f"{greeting(name, rng)} {INTRO}"


def link_line(url: str) -> str:
    """A link is its URL, written once (Mohammad, 2026-11-06): no markdown label, nothing doubled."""
    return url


def money(amount, currency: str) -> str:
    return f"{fa_number(amount)} {CURRENCY_FA.get((currency or '').upper(), '')}".strip()


REPOSTER_LINE = "دیدیم چند بار آگهی‌تون رو گذاشتید. اینجا لازم نیست هی بالا بیاریدش."


# ---------------------------------------------------------------- first messages: the hooks (Mohammad, 2026-11-06)
# Line 1 carries the value (the notification preview shows only the start); line 2 starts «از تیم چمدونم».
# No emoji, no link, no @mention, no video. Every number is real; a number under MIN_COUNT drops its line.
# Word for word as projects/chamedoon/OUTREACH-HOOKS.md has them.

MIN_COUNT = 3
TEAM = "از تیم چمدونم."
NO_SEARCHING = "نیازی نیست همه‌ی گروه‌های خونه رو دونه دونه بگردید؛ توی چمدونِ خانه می‌تونید آگهی‌ها رو بر اساس نیازتون فیلتر کنید"


def hello(name: str) -> str:
    return f"سلام {name} جان،" if name else "سلام وقت بخیر،"


def s1(name: str, city: str, *, budget, currency: str, count: int, teaser: str) -> tuple[str, str]:
    budget_fa = f"{fa_number(budget)} {CURRENCY_FA.get((currency or '').upper(), '')}".strip()
    head = f"{hello(name)} {fa_number(count)} تا خونه تو {city_fa(city)} پیدا کردیم که به بودجه‌ی {budget_fa}ی‌تون می‌خوره"
    head += f"؛ یکیش {teaser}." if teaser else "."
    return f"{head}\n{TEAM} {NO_SEARCHING} و راحت‌تر دنبال چیزی که لازم دارید بگردید. بفرستم ببینید؟", "h"


def s1b(name: str, city: str, *, week_count: int) -> tuple[str, str]:
    tail = f"{TEAM} {NO_SEARCHING}. بودجه‌تون حدوداً چقدره تا اونایی که می‌خوره رو براتون جدا کنم؟"
    if week_count >= MIN_COUNT:
        return f"سلام وقت بخیر، این هفته {fa_number(week_count)} تا آگهی خونه‌ی {city_fa(city)} رو از گروه‌های ایرانی یه‌جا جمع کردیم.\n{tail}", "h"
    return f"سلام وقت بخیر، دیدم تو {city_fa(city)} دنبال خونه‌اید.\n{tail}", "h0"


def s2(name: str, city: str) -> tuple[str, str]:
    place = city_fa(city)
    return (
        f"سلام وقت بخیر، دیدم تو {place} دنبال خونه‌اید؛ ما هر روز گروه‌های ایرانی {place} رو می‌گردیم.\n"
        f"{TEAM} نیازی نیست همه‌ی گروه‌های خونه رو دونه دونه بگردید. الان چیزی که بخوره نیست؛ می‌خواید اولین آگهی مناسب که اومد همون لحظه براتون بفرستم؟",
        "h",
    )


def p1_demand(name: str, city: str, *, ad_desc: str, seekers_week: int) -> tuple[str, str]:
    place = city_fa(city)
    return (
        f"{hello(name)} آگهی {ad_desc} تو {place} رو رایگان تو چمدون هم گذاشتیم؛ این هفته {fa_number(seekers_week)} نفر تو {place} دنبال خونه پست گذاشتن.\n"
        f"{TEAM} هنوز موجوده؟ اگه آره لینکش رو بفرستم که خودتون هم ببینید.",
        "demand",
    )


def p1_repost(name: str, city: str) -> tuple[str, str]:
    return (
        f"{hello(name)} دیدم آگهی خونه‌تون تو {city_fa(city)} رو چند بار تو گروه‌ها گذاشتید. تو چمدون هم گذاشتیمش و همون‌جا می‌مونه؛ لازم نیست هر روز دوباره بذاریدش.\n"
        f"{TEAM} هنوز موجوده؟",
        "repost",
    )


def p1_gap(name: str, city: str, *, gap: str) -> tuple[str, str]:
    place = city_fa(city)
    if gap == "photo":
        return (
            f"{hello(name)} آگهی خونه‌تون تو {place} رو تو چمدون گذاشتیم، فقط عکس نداره؛ آگهی‌های عکس‌دار معمولاً خیلی بیشتر پیام می‌گیرن.\n"
            f"{TEAM} اگه چند تا عکس همین‌جا بفرستید، خودم اضافه می‌کنم.",
            "photo",
        )
    return (
        f"{hello(name)} آگهی خونه‌تون تو {place} رو تو چمدون گذاشتیم، فقط قیمت نداره؛ آگهی بی‌قیمت به کسایی که با بودجه‌ی مشخص دنبال خونه‌ان نشون داده نمی‌شه.\n"
        f"{TEAM} قیمتش چنده؟ خودم ثبتش می‌کنم.",
        "price",
    )


def p1(rng, name: str, city: str, *, url: str, reposter: bool = False) -> tuple[str, str]:
    """The old P1 with its link. Kept for reference; new sends use the hooks above."""
    bodies = (
        ("a", "آگهی خونه‌تون تو {city} رو دیدیم و رایگان تو چمدون هم گذاشتیمش:"),
        ("b", "آگهی خونه‌تون تو {city} رو دیدیم و رایگان تو چمدون هم گذاشتیم:"),
    )
    variant, body = rng.choice(bodies)
    lines = [f"{opening(name, rng)} {body.format(city=city_fa(city))}", link_line(url), "با ورود، آگهی تو لیست خودتون میاد، می‌تونید ویرایشش کنید و ببینید چند نفر دیدنش."]
    if reposter:
        lines.append(REPOSTER_LINE)
    return "\n".join(lines), variant


def p1q(rng, name: str, city: str, *, reposter: bool = False) -> tuple[str, str]:
    bodies = (
        ("a", "آگهی خونه‌تون تو {city} رو تو چمدون هم گذاشتیم و می‌خوایم مطمئن بشیم به‌روزه. هنوز موجوده؟"),
        ("b", "آگهی خونه‌تون تو {city} رو رایگان تو چمدون هم گذاشتیم و می‌خوایم مطمئن بشیم به‌روزه. هنوز موجوده؟"),
    )
    variant, body = rng.choice(bodies)
    text = f"{opening(name, rng)} {body.format(city=city_fa(city))}"
    return (f"{text}\n{REPOSTER_LINE}" if reposter else text), variant


def p5(rng, name: str, city: str) -> tuple[str, str]:
    """In a chat we already have: a greeting, never «از تیم چمدون پیام می‌دم» again (rule 18)."""
    body = "آگهی خونه‌تون تو {city} رو تو چمدون داریم و تا چند روز دیگه منقضی می‌شه. هنوز موجوده؟"
    return f"{hello(name)} {body.format(city=city_fa(city))}", "b"


def p3(views: int, saves: int, contacts: int, url: str) -> tuple[str, str]:
    """Only the numbers that exist. The caller has checked the threshold; the words say «got the contact», never «called»."""
    parts = []
    if views:
        parts.append(f"{fa_number(views)} بازدید")
    if saves:
        parts.append(f"{fa_number(saves)} نشان‌کردن")
    head = " و ".join(parts)
    line = f"آگهی‌تون این هفته {head} داشت" if head else "آگهی‌تون این هفته"
    if contacts:
        line += f"{' و ' if head else ' '}{fa_number(contacts)} نفر اطلاعات تماستون رو گرفتن"
    return f"{line}: {link_line(url)}", "a"


def p6(rng, name: str, city: str, *, count: int, url: str) -> tuple[str, str]:
    bodies = (
        ("a", "{count} تا آگهی شما تو {city} رو رایگان تو چمدون گذاشتیم. با ورود همه‌شون رو یه‌جا می‌بینید و ویرایش می‌کنید:"),
        ("b", "{count} تا از آگهی‌هاتون تو {city} رو رایگان تو چمدون گذاشتیم. با ورود همه‌شون رو یه‌جا می‌بینید و ویرایش می‌کنید:"),
    )
    variant, body = rng.choice(bodies)
    return f"{opening(name, rng)} {body.format(count=fa_number(count), city=city_fa(city))} {link_line(url)}", variant


def c2(rng, name: str, origin: str, destination: str, url: str) -> tuple[str, str]:
    bodies = (
        ("a", "سفرتون از {origin} به {destination} رو تو چمدون هم گذاشتیم. اینجا آگهی بار رایگانه:"),
        ("b", "سفرتون از {origin} به {destination} رو رایگان تو چمدون هم گذاشتیم. آگهی بار اینجا رایگانه:"),
    )
    variant, body = rng.choice(bodies)
    return f"{opening(name, rng)} {body.format(origin=origin, destination=destination)} {link_line(url)}", variant


def c3(rng, name: str, origin: str, destination: str, url: str) -> tuple[str, str]:
    bodies = (
        ("a", "نوروز نزدیکه و خیلی‌ها بار می‌فرستن. دیدم مسیر {origin} به {destination} رو دارید. تو چمدون مسافر و فرستنده این مسیر رو رایگان پیدا می‌کنن:"),
        ("b", "نزدیک نوروز خیلی‌ها دنبال مسافرن. مسیر {origin} به {destination} رو تو چمدون رایگان می‌تونید ببینید:"),
    )
    variant, body = rng.choice(bodies)
    return f"{opening(name, rng)} {body.format(origin=origin, destination=destination)} {link_line(url)}", variant
