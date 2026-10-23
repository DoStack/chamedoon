"""Post listings to the housing channels: one Persian post per listing, its first photo and three buttons.

Only listings the review trusted are posted: active, not queued for review, not a
repost. A listing is posted once. A new channel is not flooded: only listings seen in
the last few days go out, a few per run, up to a daily cap. Housing goes to its own
channels through its own bot and never to the courier channel.
"""

from __future__ import annotations

import html
import logging
import math
import time
from datetime import timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from home import bot, conf, deeplink
from home.guard import is_courier_channel
from home.models import (
    HomeChannel,
    HomeListing,
    HomePublication,
    ListerType,
    ListingKind,
    ListingStatus,
    MediaStatus,
    PostStatus,
)
from home.storage import get_storage

logger = logging.getLogger(__name__)

CAPTION_LIMIT = 1024
MESSAGE_LIMIT = 4096
MAX_ATTEMPTS = 3
PER_POST_RESERVE_SECONDS = 4

PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
MONTHS = ["ژانویه", "فوریه", "مارس", "آوریل", "مه", "ژوئن", "ژوئیه", "اوت", "سپتامبر", "اکتبر", "نوامبر", "دسامبر"]
TYPE_FA = {
    "room": "اتاق در خانه مشترک",
    "studio": "استودیو",
    "apartment": "آپارتمان",
    "house": "خانه",
    "basement": "بیسمنت",
    "sublet": "سابلت",
    "lease_transfer": "انتقال لیز",
    "short_stay": "اقامت کوتاه‌مدت",
    "other": "ملک",
}
CURRENCY_SYMBOL = {"EUR": "€", "CAD": "CA$", "USD": "US$", "GBP": "£"}
PERIOD_FA = {"month": "در ماه", "week": "در هفته", "night": "برای هر شب"}
BASIS_FA = {"warm": "گرم، با قبوض", "cold": "سرد، بدون قبوض"}
FURNISHED_FA = {"yes": "مبله", "partial": "نیمه‌مبله", "no": "بدون مبل"}
GENDER_FA = {"female": "فقط خانم", "male": "فقط آقا", "couple": "مناسب زوج"}
REGION_WARNING_FA = {
    "ON": "در انتاریو فقط ودیعه‌ی ماه آخر مجاز است و ودیعه‌ی خسارت قانونی نیست.",
    "QC": "در کبک مالک نمی‌تواند ودیعه بگیرد.",
}
# Persian names of the metros with a channel and a few other big cities; any other city stays as written.
CITY_FA = {
    "toronto": "تورنتو",
    "montreal": "مونترال",
    "vancouver": "ونکوور",
    "ottawa": "اتاوا",
    "calgary": "کلگری",
    "berlin": "برلین",
    "hamburg": "هامبورگ",
    "cologne": "کلن",
    "köln": "کلن",
    "munich": "مونیخ",
    "münchen": "مونیخ",
    "frankfurt": "فرانکفورت",
}
TERM_FA = {
    "sublet": "سابلت",
    "lease_transfer": "انتقال لیز",
    "short_stay": "اقامت کوتاه‌مدت",
    "temporary": "اجاره‌ی موقت",
    "lease": "اجاره‌ی بلندمدت",
}
# Flags the review writes (home/review.py BOOL_FLAGS) that a renter looks for, with the value that is shown.
AMENITY_FLAGS = (
    ("parking", True, "🚗 پارکینگ"),
    ("pets_ok", True, "🐾 حیوان خانگی مجاز"),
    ("pets_ok", False, "بدون حیوان خانگی"),
    ("no_smoking", True, "🚭 سیگار ممنوع"),
    ("couples_ok", True, "مناسب زوج"),
    ("students_only", True, "فقط دانشجو"),
)
CONDITION_FLAGS = (
    ("first_last", "ماه اول و آخر"),
    ("credit_check", "کردیت چک"),
    ("wgb", "WBS لازم"),
)
DETAILS_BUTTON = "جزئیات و عکس‌ها"


def fa(value) -> str:
    return str(value).translate(PERSIAN_DIGITS)


def num(value: Decimal) -> str:
    """A decimal as plain digits: 60 stays 60 (never 6E+1) and 16.0 becomes 16."""
    return format(value.normalize(), "f")


def esc(value) -> str:
    return html.escape(str(value or ""), quote=False)


def money(amount: Decimal | None, currency: str) -> str:
    if amount is None:
        return ""
    number = f"{amount:,.0f}" if amount == amount.to_integral_value() else f"{amount:,.2f}"
    symbol = CURRENCY_SYMBOL.get(currency, currency)
    return f"{number} {symbol}" if symbol in CURRENCY_SYMBOL.values() else f"{number} {symbol}"


def persian_date(value) -> str:
    return f"{fa(value.day)} {MONTHS[value.month - 1]} {fa(value.year)}"


def tag(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in text.strip()).strip("_")
    return f"#{cleaned}" if cleaned else ""


def city_fa(name: str) -> str:
    return CITY_FA.get((name or "").strip().casefold(), name or "")


def term(listing: HomeListing) -> str:
    """Sublet, lease transfer, short stay, temporary or long lease, from the type, dates and flags. Blank if unknown."""
    flags = listing.flags or {}
    if listing.property_type in {"sublet", "lease_transfer", "short_stay"}:
        return listing.property_type
    if listing.price_period in {"night", "week"}:
        return "short_stay"
    if flags.get("untermiete"):
        return "sublet"
    if flags.get("temporary") or listing.available_to:
        return "temporary"
    if (listing.min_term_months or 0) >= 12 or flags.get("first_last"):
        return "lease"
    return ""


def place_lines(listing: HomeListing, metro: str) -> list[tuple[int, str]]:
    """The city (metro first, then the suburb when it differs) and, on its own line, the neighbourhood."""
    metro = metro or listing.city or ""
    seen = {metro.casefold()} if metro else set()
    city_parts = [f"<b>{esc(city_fa(metro))}</b>"] if metro else []
    if listing.city and listing.city.casefold() not in seen:
        city_parts.append(esc(listing.city))
        seen.add(listing.city.casefold())
    lines = []
    area = (listing.area or "").strip()
    if area and area.casefold() not in seen:
        if not listing.city and metro:
            # A blank city routed by its area: the area is the suburb, not a neighbourhood.
            city_parts.append(esc(area))
        else:
            lines.append((1, f"🏘 محله: {esc(area)}"))
    if city_parts:
        lines.insert(0, (0, "📍 " + " · ".join(city_parts)))
    return lines


def amenities(listing: HomeListing) -> list[str]:
    items = []
    if listing.furnished in FURNISHED_FA:
        items.append(FURNISHED_FA[listing.furnished])
    if listing.bills_included == "yes":
        items.append("قبوض داخل قیمت")
    elif listing.bills_included == "no":
        items.append("قبوض جدا")
    if listing.registration == "yes":
        items.append("ملد ✅")
    elif listing.registration == "no":
        items.append("بدون ملد")
    flags = listing.flags or {}
    items += [label for key, value, label in AMENITY_FLAGS if flags.get(key) is value]
    return items


def listing_lines(listing: HomeListing, *, phone: bool, metro: str = "", photos: int = 0) -> list[tuple[int, str]]:
    """Lines of a post with a priority; lower numbers are dropped last when a caption is too long."""
    lines: list[tuple[int, str]] = []
    flags = listing.flags or {}
    type_fa = TYPE_FA.get(listing.property_type, "ملک")
    kind = TERM_FA.get(term(listing), "")
    if listing.kind == ListingKind.WANTED:
        title = type_fa if listing.property_type != "other" else "خونه"
        lines.append((0, f"🔎 <b>دنبال {esc(title)}</b>"))
        lines += place_lines(listing, metro)
        if listing.price_max:
            lines.append((1, f"💰 بودجه تا <b>{esc(money(listing.price_max, listing.price_currency))}</b>"))
    else:
        title = f"{type_fa} · {kind}" if kind and kind != type_fa else type_fa
        lines.append((0, f"🏠 <b>{esc(title)}</b>"))
        lines += place_lines(listing, metro)
        if listing.price_amount:
            extra = []
            period = PERIOD_FA.get(listing.price_period, "")
            if period:
                extra.append(period)
            if listing.price_basis in BASIS_FA:
                extra.append(BASIS_FA[listing.price_basis])
            tail = f" ({esc('، '.join(extra[1:]))})" if len(extra) > 1 else ""
            head = f" {esc(extra[0])}" if extra else ""
            lines.append((0, f"💶 <b>{esc(money(listing.price_amount, listing.price_currency))}</b>{head}{tail}"))
        else:
            lines.append((1, "💶 قیمت: از آگهی‌دهنده بپرسید"))
        if listing.deposit_amount:
            lines.append((2, f"🔐 ودیعه: {esc(money(listing.deposit_amount, listing.price_currency))}"))
        elif listing.deposit_months:
            lines.append((2, f"🔐 ودیعه: {fa(num(listing.deposit_months))} ماه اجاره"))
    facts = []
    if listing.size_m2:
        facts.append(f"📐 {fa(num(listing.size_m2))} متر")
    if listing.rooms and listing.kind == ListingKind.OFFER:
        facts.append(f"🛏 {fa(num(listing.rooms))} اتاق")
    if facts:
        lines.append((1, " · ".join(esc(item) for item in facts)))
    extras = amenities(listing)
    if extras:
        lines.append((1, "✨ " + " · ".join(esc(item) for item in extras)))
    conditions = [label for key, label in CONDITION_FLAGS if flags.get(key)]
    if conditions:
        lines.append((2, "📋 شرایط: " + " · ".join(esc(item) for item in conditions)))
    dates = []
    if listing.available_from:
        dates.append(f"از {persian_date(listing.available_from)}")
    if listing.available_to:
        dates.append(f"تا {persian_date(listing.available_to)}")
    if listing.min_term_months:
        dates.append(f"حداقل {fa(listing.min_term_months)} ماه")
    if dates:
        lines.append((1, "📅 " + " · ".join(esc(item) for item in dates)))
    if listing.roommate_gender in GENDER_FA:
        lines.append((1, f"👤 {GENDER_FA[listing.roommate_gender]}"))
    if listing.kind == ListingKind.OFFER and listing.lister_type == ListerType.AGENT:
        lines.append((2, "🏢 بنگاه یا آگهی‌دهنده‌ی پرتکرار"))
    elif listing.kind == ListingKind.OFFER and listing.lister_type == ListerType.OWNER:
        lines.append((2, "🧾 مالک یا مستاجر"))
    if listing.summary_fa:
        lines.append((3, f"<i>{esc(fa(listing.summary_fa))}</i>"))
    warning = REGION_WARNING_FA.get(listing.region) if flags.get("deposit_warning") else ""
    if warning:
        lines.append((2, f"⚠️ {esc(warning)}"))
    contact = []
    if listing.contact_telegram:
        contact.append(f"@{esc(listing.contact_telegram)}")
    if phone and listing.contact_phone:
        contact.append(f"<code>{esc(listing.contact_phone)}</code>")
    post = listing.post
    link = post.source_url if post.source_url and "/c/" not in post.source_url else ""
    if contact:
        lines.append((0, "📞 تماس: " + " · ".join(contact)))
    elif link:
        lines.append((0, f'📞 تماس: در <a href="{esc(link)}">پست اصلی</a>'))
    else:
        lines.append((0, "📞 تماس: از طریق ربات خانه (به‌زودی)"))
    if contact and link:
        lines.append((2, f'<a href="{esc(link)}">پست اصلی</a>'))
    if photos > 1:
        # A text post whose photos Telegram refused: the photos are still one tap away, in the Mini App.
        lines.append((1, f"🖼 {fa(photos)} عکس در «{DETAILS_BUTTON}»"))
    hashtags = " ".join(
        item
        for item in (
            tag(metro or listing.city),
            tag(type_fa) if listing.kind == ListingKind.OFFER else "#دنبال_خانه",
            tag(kind) if kind and kind != type_fa and listing.kind == ListingKind.OFFER else "",
        )
        if item
    )
    if hashtags:
        lines.append((3, hashtags))
    return lines


def format_listing_text(
    listing: HomeListing,
    *,
    limit: int = MESSAGE_LIMIT,
    phone: bool | None = None,
    metro: str | None = None,
    photos: int = 0,
) -> str:
    """The Persian post for a listing. Over the limit, the lowest-priority lines go first."""
    if phone is None:
        phone = conf.publish_phone()
    if metro is None:
        metro = metro_of(listing)
    lines = listing_lines(listing, phone=phone, metro=metro, photos=photos)
    while True:
        text = "\n".join(line for _priority, line in lines)
        if len(text) <= limit or len(lines) <= 1:
            return text[:limit]
        worst = max(priority for priority, _ in lines)
        for index in range(len(lines) - 1, -1, -1):
            if lines[index][0] == worst:
                del lines[index]
                break


def metro_of(listing: HomeListing, channel: HomeChannel | None = None) -> str:
    """The metro of a listing: the city channel's city, else the city channel it routes to, else its own city."""
    if channel is not None and channel.city:
        return channel.city
    found = route(listing, city_channels())
    return found.city if found else listing.city


def price_band(listing: HomeListing) -> tuple[int, int] | None:
    """Rent band for "similar": 80–120% of an offer's price, or up to a wanted post's budget, in steps of 50."""
    if listing.kind == ListingKind.WANTED:
        return (0, math.ceil(listing.price_max / 50) * 50) if listing.price_max else None
    if not listing.price_amount:
        return None
    return math.floor(listing.price_amount * Decimal("0.8") / 50) * 50, math.ceil(listing.price_amount * Decimal("1.2") / 50) * 50


def post_buttons(listing: HomeListing, channel: HomeChannel, metro: str | None = None) -> list[list[dict]]:
    """The keyboard under a post: details (the listing), similar (city + type + rent band) and city alerts."""
    metro = metro_of(listing, channel) if metro is None else metro
    source = deeplink.source_tag(channel.country, channel.city)
    property_type = listing.property_type if listing.property_type != "other" else ""
    details = deeplink.build(listing.country, metro, listing=listing.pk, source=source)
    similar = deeplink.build(listing.country, metro, type=property_type, band=price_band(listing), source=source)
    return [
        [{"text": DETAILS_BUTTON, "url": conf.mini_app_link(start=details)}],
        [
            {"text": "مشابه‌ها", "url": conf.mini_app_link(start=similar)},
            {"text": "🔔 گوش به زنگ", "url": conf.bot_start_link(deeplink.alert_start(listing.country, metro))},
        ],
    ]


def photo_urls(listing: HomeListing) -> list[str]:
    storage = get_storage()
    if storage is None:
        return []
    keys = listing.post.media.filter(status=MediaStatus.STORED).order_by("position").values_list("storage_key", flat=True)
    return [storage.url(key) for key in keys[:10] if key]


def waiting_for_photos(listing: HomeListing) -> bool:
    """Photos are still being fetched; hold the post so it goes out with its photo and the full photo count."""
    return get_storage() is not None and listing.post.media.filter(status=MediaStatus.PENDING).exists()


def publishable():
    window = conf.publish_window_days()
    channel_countries = HomeChannel.objects.filter(enabled=True, city="").values_list("country", flat=True)
    return (
        HomeListing.objects.filter(
            status=ListingStatus.ACTIVE,
            needs_review=False,
            published_at__isnull=True,
            publish_attempts__lt=MAX_ATTEMPTS,
            post__status=PostStatus.ACCEPTED,
            last_seen__gte=timezone.now() - timedelta(days=window),
            country__in=list(channel_countries),
        )
        .select_related("post", "post__source")
        .order_by("first_seen", "id")
    )


def publish_preview(limit: int = 5) -> list[dict]:
    """What the next posts would look like. Sends nothing."""
    rows = []
    for listing in publishable()[:limit]:
        photos = len(photo_urls(listing))
        rows.append(
            {
                "id": listing.pk,
                "country": listing.country,
                "text": format_listing_text(listing, limit=CAPTION_LIMIT),
                "photos": photos,
                "waiting_for_photos": waiting_for_photos(listing),
            }
        )
    return rows


def _posted_last_day(channel: HomeChannel) -> int:
    return HomeListing.objects.filter(channel=channel, published_at__gte=timezone.now() - timedelta(days=1)).count()


def _send(listing: HomeListing, channel: HomeChannel) -> tuple[list[int], str]:
    """Send one listing to one channel. Returns the message ids and a note. Raises BotError.

    Every post carries the buttons. A text or single-photo post has them on the post; the
    Bot API puts no buttons on an album, so under an album they go out as a short reply,
    the same way the glass button has been sent since c7a31e1.
    """
    if is_courier_channel(channel.username, channel.chat_id):
        raise bot.BotError("This is the courier channel. Housing never posts there.")
    target = channel.target()
    urls = photo_urls(listing)
    metro = metro_of(listing, channel)
    buttons = post_buttons(listing, channel, metro)
    note = ""
    ids: list[int] = []
    if urls:
        caption = format_listing_text(listing, limit=CAPTION_LIMIT, metro=metro)
        try:
            if len(urls) == 1:
                ids = bot.send_photo(target, urls[0], caption, buttons=buttons)
            else:
                # Albums cannot carry buttons, so the keyboard goes out as a reply right under the album.
                ids = bot.send_album(target, urls, caption)
                try:
                    ids.extend(bot.send_message(target, f"👆 {DETAILS_BUTTON}", buttons=buttons, reply_to=ids[0]))
                except bot.BotError as exc:
                    note = f"buttons skipped: {exc.description}"[:300]
        except bot.BotError as exc:
            if exc.retry_after or exc.code in {401, 403, 429}:
                raise
            note = f"photos skipped: {exc.description}"[:300]
    if not ids:
        ids = bot.send_message(target, format_listing_text(listing, metro=metro, photos=len(urls)), buttons=buttons)
    return ids, note


def publish_listing(listing: HomeListing, channel: HomeChannel) -> list[int]:
    """Send one listing to its country channel. Returns the message ids. Raises BotError."""
    ids, note = _send(listing, channel)
    listing.channel = channel
    listing.channel_message_id = ids[0]
    listing.channel_message_ids = ids
    listing.published_at = timezone.now()
    listing.publish_error = note
    listing.save(
        update_fields=[
            "channel",
            "channel_message_id",
            "channel_message_ids",
            "published_at",
            "publish_error",
            "updated_at",
        ]
    )
    return ids


def city_channels() -> list[HomeChannel]:
    return list(HomeChannel.objects.filter(enabled=True).exclude(city=""))


def route(listing: HomeListing, channels: list[HomeChannel]) -> HomeChannel | None:
    """The city channel of a listing: its city or area (or a comma-separated part of them) is the metro or an alias."""
    wanted = set()
    for value in (listing.city or "", listing.area or ""):
        for part in [value, *value.split(",")]:
            if part.strip():
                wanted.add(part.strip().casefold())
    for channel in channels:
        if channel.country == listing.country and wanted & channel.names():
            return channel
    return None


def city_publishable(channels: list[HomeChannel] | None = None) -> list[tuple[HomeListing, HomeChannel]]:
    """Listings still to post to their city channel, oldest first."""
    channels = city_channels() if channels is None else channels
    if not channels:
        return []
    window = conf.publish_window_days()
    done = set(
        HomePublication.objects.filter(
            Q(published_at__isnull=False) | Q(attempts__gte=MAX_ATTEMPTS), channel__in=channels
        ).values_list("listing_id", "channel_id")
    )
    candidates = (
        HomeListing.objects.filter(
            status=ListingStatus.ACTIVE,
            needs_review=False,
            post__status=PostStatus.ACCEPTED,
            last_seen__gte=timezone.now() - timedelta(days=window),
            country__in={channel.country for channel in channels},
        )
        .select_related("post", "post__source")
        .order_by("first_seen", "id")
    )
    rows = []
    for listing in candidates:
        channel = route(listing, channels)
        if channel is None or (listing.pk, channel.pk) in done:
            continue
        if channel.active_from and listing.published_at and listing.published_at < channel.active_from:
            # Already in the country feed before the city channel went live: it was copied by hand.
            continue
        rows.append((listing, channel))
    return rows


def publish_to_city(listing: HomeListing, channel: HomeChannel) -> list[int]:
    """Send one listing to its city channel and record it. Raises BotError."""
    ids, note = _send(listing, channel)
    HomePublication.objects.update_or_create(
        listing=listing, channel=channel, defaults={"message_ids": ids, "published_at": timezone.now(), "error": note}
    )
    return ids


def publish_pending(*, limit: int | None = None, stop_at: float | None = None) -> dict:
    """Post the next listings, a few per run. Never raises on Telegram trouble."""
    if not conf.publish_ready():
        return {"ok": True, "skipped": conf.publish_blocker(), "published": 0}
    limit = limit or conf.publish_batch()
    channels = {channel.country: channel for channel in HomeChannel.objects.filter(enabled=True, city="")}
    posted_today = {country: _posted_last_day(channel) for country, channel in channels.items()}
    cap = conf.publish_daily_cap()
    published = failed = held = capped = 0
    retry_after = 0
    # Each listing goes to its city channel right after its country channel, so a long country
    # backlog cannot use up the run's time before any city post goes out.
    cities = city_channels()
    city = {"published": 0, "failed": 0, "held_for_photos": 0, "over_daily_cap": 0}
    city_today: dict[int, int] = {}
    for listing in publishable():
        if published + failed >= limit:
            break
        if stop_at is not None and time.monotonic() + PER_POST_RESERVE_SECONDS >= stop_at:
            break
        channel = channels[listing.country]
        if posted_today[listing.country] >= cap:
            capped += 1
            continue
        if waiting_for_photos(listing):
            held += 1
            continue
        try:
            publish_listing(listing, channel)
        except bot.BotError as exc:
            failed += 1
            listing.publish_attempts += 1
            listing.publish_error = exc.description[:300]
            listing.save(update_fields=["publish_attempts", "publish_error", "updated_at"])
            channel.last_error = exc.description[:300]
            channel.save(update_fields=["last_error"])
            logger.warning("Home publish failed for listing %s: %s", listing.pk, exc.description)
            if exc.retry_after:
                retry_after = exc.retry_after
                break
            continue
        posted_today[listing.country] += 1
        published += 1
        metro = route(listing, cities)
        if metro is not None and not HomePublication.objects.filter(listing=listing, channel=metro).exists():
            retry_after = _city_post(listing, metro, city, city_today)
            if retry_after:
                break
    if not retry_after:
        city, retry_after = _publish_cities(limit, stop_at, city, city_today)
    return {
        "city": city,
        "ok": True,
        "published": published,
        "failed": failed,
        "held_for_photos": held,
        "over_daily_cap": capped,
        "retry_after": retry_after,
        "remaining": publishable().count(),
    }


def _city_post(listing: HomeListing, channel: HomeChannel, counts: dict, posted_today: dict[int, int]) -> int:
    """Post one listing to one city channel within its daily cap. Returns Telegram's retry_after, or 0."""
    if channel.pk not in posted_today:
        posted_today[channel.pk] = HomePublication.objects.filter(
            channel=channel, published_at__gte=timezone.now() - timedelta(days=1)
        ).count()
    if posted_today[channel.pk] >= conf.publish_daily_cap():
        counts["over_daily_cap"] += 1
        return 0
    if waiting_for_photos(listing):
        counts["held_for_photos"] += 1
        return 0
    try:
        publish_to_city(listing, channel)
    except bot.BotError as exc:
        counts["failed"] += 1
        row, _created = HomePublication.objects.get_or_create(listing=listing, channel=channel)
        row.attempts += 1
        row.error = exc.description[:300]
        row.save(update_fields=["attempts", "error"])
        channel.last_error = exc.description[:300]
        channel.save(update_fields=["last_error"])
        logger.warning("Home city publish failed for listing %s: %s", listing.pk, exc.description)
        return exc.retry_after
    posted_today[channel.pk] += 1
    counts["published"] += 1
    return 0


def _publish_cities(limit: int, stop_at: float | None, counts: dict | None = None, posted_today: dict | None = None) -> tuple[dict, int]:
    """Post the next listings to their city channels. Same batch size and daily cap per channel."""
    counts = counts if counts is not None else {"published": 0, "failed": 0, "held_for_photos": 0, "over_daily_cap": 0}
    posted_today = posted_today if posted_today is not None else {}
    for listing, channel in city_publishable():
        if counts["published"] + counts["failed"] >= limit:
            break
        if stop_at is not None and time.monotonic() + PER_POST_RESERVE_SECONDS >= stop_at:
            break
        retry_after = _city_post(listing, channel, counts, posted_today)
        if retry_after:
            return counts, retry_after
    return counts, 0


def verify_channel(channel: HomeChannel) -> str:
    """Check the bot can post in the channel. Saves the chat id. Returns an empty string, or the problem."""
    if is_courier_channel(channel.username, channel.chat_id):
        channel.last_error = "This is the courier channel. Housing never posts there."
        channel.save(update_fields=["last_error"])
        return channel.last_error
    try:
        found = bot.check_channel(channel.username)
    except bot.BotError as exc:
        channel.last_error = exc.description[:300]
        channel.save(update_fields=["last_error"])
        return channel.last_error
    if is_courier_channel(channel.username, found["chat_id"]):
        channel.last_error = "This is the courier channel. Housing never posts there."
        channel.save(update_fields=["last_error"])
        return channel.last_error
    if found.get("bot"):
        settings_row = conf.row()
        if settings_row.bot_username != found["bot"]:
            settings_row.bot_username = found["bot"]
            settings_row.save(update_fields=["bot_username", "updated_at"])
    channel.chat_id = found["chat_id"]
    channel.title = found["title"]
    channel.verified_at = timezone.now()
    channel.last_error = ""
    channel.save(update_fields=["chat_id", "title", "verified_at", "last_error"])
    return ""
