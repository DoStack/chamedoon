"""Ready posts for the admin: posts already published in our channels, with link and view count.

Intro and guide come from adminyar.ReadyPost (filled in Django admin). Weekly, monthly and country come from
home_contentpost (the content runner's records): only the newest sent post per channel. The bot never posts to a
channel or group; it only forwards a channel post to the admin's own private chat.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

import jdatetime
from django.utils import timezone

from adminyar.models import ReadyCategory, ReadyPost
from home.content.text import city_fa
from home.models import ContentKind, HomeChannel, HomeContentPost

COUNTRY_FA = {"CA": "کانادا", "DE": "آلمان"}
CATEGORIES = [
    ("intro", "👋", "معرفی"),
    ("guide", "📖", "راهنما"),
    ("weekly", "📈", "آمار هفتگی"),
    ("monthly", "🗓", "آمار ماهانه"),
    ("country", "🌍", "آمار کشور"),
]
ICON = {code: icon for code, icon, _ in CATEGORIES}
NAME = {code: name for code, _, name in CATEGORIES}
CONTENT_KINDS = {"weekly": ContentKind.WEEKLY, "monthly": ContentKind.MONTHLY, "country": ContentKind.COUNTRY}
# Mohammad rejected the scam-safety tips post (decisions.md 2026-11-11): never offered, whatever its row says.
BLOCKED = re.compile(r"کلاهبردار|کلاه‌بردار|scam|نکات امنیتی|tips", re.IGNORECASE)
FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
G_MONTHS = ["ژانویه", "فوریه", "مارس", "آوریل", "مه", "ژوئن", "ژوئیه", "اوت", "سپتامبر", "اکتبر", "نوامبر", "دسامبر"]
J_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]


def fa(value) -> str:
    return str(value).translate(FA_DIGITS)


def num(value: int) -> str:
    return fa(f"{value:,}").replace(",", "٬")


def blocked(title: str) -> bool:
    return bool(BLOCKED.search(title or ""))


@dataclass
class Item:
    category: str
    channel: HomeChannel
    message_ids: list[int]
    title: str
    ref: str
    period: str = ""
    views: int | None = None
    views_at: datetime | None = None

    @property
    def link(self) -> str:
        return f"https://t.me/{self.channel.username}/{self.message_ids[0]}"


def place(channel: HomeChannel) -> str:
    return city_fa(channel.city) if channel.city else COUNTRY_FA.get(channel.country, channel.country)


def period_label(key: str) -> str:
    """Gregorian first, Shamsi second (CONTENT-SPEC). 2026-W46 -> the Mon-Sun range, 202611 -> the month."""
    try:
        if "-W" in key:
            year, week = key.split("-W")
            start, end = date.fromisocalendar(int(year), int(week), 1), date.fromisocalendar(int(year), int(week), 7)
            js, je = jdatetime.date.fromgregorian(date=start), jdatetime.date.fromgregorian(date=end)
            return f"{fa(start.day)} تا {fa(end.day)} {G_MONTHS[end.month - 1]} {fa(end.year)} ({fa(js.day)} تا {fa(je.day)} {J_MONTHS[je.month - 1]} {fa(je.year)})"
        if len(key) == 6 and key.isdigit():
            start = date(int(key[:4]), int(key[4:]), 1)
            js = jdatetime.date.fromgregorian(date=start)
            return f"{G_MONTHS[start.month - 1]} {fa(start.year)} ({J_MONTHS[js.month - 1]} {fa(js.year)})"
    except (ValueError, IndexError):
        pass
    return fa(key)


def _ready(category: str) -> list[Item]:
    rows = ReadyPost.objects.filter(category=category, enabled=True, channel__enabled=True).select_related("channel")
    return [Item(category, row.channel, [int(row.message_id)], row.title, f"r{row.pk}", views=row.views, views_at=row.views_at) for row in rows if not blocked(row.title)]


def _content(category: str) -> list[Item]:
    """The newest sent post of this kind per channel."""
    rows = (
        HomeContentPost.objects.filter(kind=CONTENT_KINDS[category], sent_at__isnull=False, message_id__isnull=False, channel__isnull=False, channel__enabled=True)
        .select_related("channel")
        .order_by("channel_id", "-sent_at")
    )
    newest: dict[int, Item] = {}
    for row in rows:
        if row.channel_id in newest or not row.channel.username:
            continue
        ids = [int(i) for i in (row.message_ids or []) if str(i).isdigit()] or [int(row.message_id)]
        newest[row.channel_id] = Item(category, row.channel, ids, NAME[category], f"c{row.pk}", period=period_label(row.period_key), views=row.views, views_at=row.views_at)
    return list(newest.values())


def items(category: str) -> list[Item]:
    if category in CONTENT_KINDS:
        return _content(category)
    if category in ReadyCategory.values:
        return _ready(category)
    return []


def channels(category: str) -> list[HomeChannel]:
    seen: dict[int, HomeChannel] = {}
    for item in items(category):
        seen.setdefault(item.channel.pk, item.channel)
    return sorted(seen.values(), key=lambda c: (c.country, c.city == "", c.city))


def for_channel(category: str, channel_id: int) -> list[Item]:
    return [item for item in items(category) if item.channel.pk == channel_id]


def by_ref(ref: str) -> Item | None:
    """The item behind a callback ref (r<ReadyPost id> or c<HomeContentPost id>), only while it may still be offered."""
    kind, pk = ref[:1], ref[1:]
    if not pk.isdigit():
        return None
    if kind == "r":
        row = ReadyPost.objects.filter(pk=int(pk)).first()
        return None if row is None else next((i for i in _ready(row.category) if i.ref == ref), None)
    if kind == "c":
        row = HomeContentPost.objects.filter(pk=int(pk)).first()
        category = next((c for c, k in CONTENT_KINDS.items() if row is not None and k == row.kind), None)
        return None if category is None else next((i for i in _content(category) if i.ref == ref), None)
    return None


def ago(when: datetime | None) -> str:
    if when is None:
        return "-"
    minutes = int((timezone.now() - when).total_seconds() // 60)
    if minutes < 60:
        return f"{fa(max(minutes, 1))} دقیقه پیش"
    if minutes < 48 * 60:
        return f"{fa(minutes // 60)} ساعت پیش"
    return f"{fa(minutes // 1440)} روز پیش"


def card(item: Item) -> str:
    title = item.title if item.category in ReadyCategory.values else NAME[item.category]
    lines = [f"{ICON[item.category]} {title} {place(item.channel)}"]
    if item.period:
        lines.append(f"📅 {item.period}")
    if item.views is not None:
        lines.append(f"👁 {num(item.views)} بازدید (به‌روز: {ago(item.views_at)})")
    else:
        lines.append("👁 بازدید هنوز خوانده نشده")
    lines.append(f"🔗 {item.link.replace('https://', '')}")
    lines.append("")
    lines.append("همین پست را از کانال فوروارد کنید تا بازدیدش بماند.")
    return "\n".join(lines)
