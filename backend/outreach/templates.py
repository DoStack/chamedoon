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


def valid_name(display_name: str, username: str = "") -> str:
    """The first word of a display name when it reads as a person's name, else nothing."""
    words = (display_name or "").strip().split()
    if not words:
        return ""
    first = words[0].strip(".,-_")
    if not _NAME.fullmatch(first) or first.casefold() in NOT_A_NAME:
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
    return f"[{url}]({url})"


def money(amount, currency: str) -> str:
    return f"{fa_number(amount)} {CURRENCY_FA.get((currency or '').upper(), '')}".strip()


REPOSTER_LINE = "دیدیم چند بار آگهی‌تون رو گذاشتید. اینجا لازم نیست هی بالا بیاریدش."


def s1(rng, name: str, city: str, *, budget, currency: str, count: int, url: str) -> tuple[str, str]:
    bodies = (
        ("a", "دیدم تو {city} دنبال خونه تا {money} می‌گردید. {count} تا آگهی پیدا کردیم که به درخواستتون می‌خوره:"),
        ("b", "درخواست خونه‌تون تو {city} تا {money} رو دیدم. {count} تا آگهی پیدا کردیم که بهش می‌خوره:"),
    )
    variant, body = rng.choice(bodies)
    text = body.format(city=city_fa(city), money=money(budget, currency), count=fa_number(count))
    return f"{opening(name, rng)} {text}\n{link_line(url)}", variant


def s1b(rng, name: str, city: str) -> tuple[str, str]:
    bodies = (
        ("a", "دیدم تو {city} دنبال خونه می‌گردید. ما آگهی گروه‌های ایرانی {city} رو یه‌جا جمع می‌کنیم. بودجه‌تون حدوداً چقدره تا آگهی‌های مناسب رو براتون بفرستم؟"),
        ("b", "درخواست خونه‌تون تو {city} رو دیدم. ما آگهی گروه‌های ایرانی {city} رو یه‌جا جمع می‌کنیم. حدوداً تا چقدر بودجه دارید که آگهی‌های مناسب رو براتون بفرستم؟"),
    )
    variant, body = rng.choice(bodies)
    return f"{opening(name, rng)} {body.format(city=city_fa(city))}", variant


def s2(rng, name: str, city: str) -> tuple[str, str]:
    bodies = (
        ("a", "دیدم تو {city} دنبال خونه می‌گردید. الان آگهی مناسبی نداریم، ولی گروه‌های ایرانی رو یه‌جا جمع می‌کنیم. می‌خواید آگهی مناسب که اومد خبرتون کنم؟"),
        ("b", "درخواست خونه‌تون تو {city} رو دیدم. فعلاً آگهی مناسبی نداریم، ولی گروه‌های ایرانی رو یه‌جا جمع می‌کنیم. آگهی مناسب که اومد خبرتون کنم؟"),
    )
    variant, body = rng.choice(bodies)
    return f"{opening(name, rng)} {body.format(city=city_fa(city))}", variant


def p1(rng, name: str, city: str, *, url: str, reposter: bool = False) -> tuple[str, str]:
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
    body = "آگهی خونه‌تون تو {city} رو تو چمدون داریم و تا چند روز دیگه منقضی می‌شه. هنوز موجوده؟"
    return f"{opening(name, rng)} {body.format(city=city_fa(city))}", "a"


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
