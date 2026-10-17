"""Post listings to the housing channels, one Persian post per listing, with its photos.

Only listings the review trusted are posted: active, not queued for review, not a
repost. A listing is posted once. A new channel is not flooded: only listings seen in
the last few days go out, a few per run, up to a daily cap. Housing goes to its own
channels through its own bot and never to the courier channel.
"""

from __future__ import annotations

import html
import logging
import time
from datetime import timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from home import bot, conf
from home.guard import is_courier_channel
from home.models import (
    HomeChannel,
    HomeListing,
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


def listing_lines(listing: HomeListing, *, phone: bool) -> list[tuple[int, str]]:
    """Lines of a post with a priority; lower numbers are dropped last when a caption is too long."""
    lines: list[tuple[int, str]] = []
    place = " · ".join(part for part in (listing.city, listing.area) if part)
    if listing.kind == ListingKind.WANTED:
        lines.append((0, f"🔎 <b>دنبال خونه: {esc(place)}</b>"))
        type_fa = TYPE_FA.get(listing.property_type, "")
        if type_fa and listing.property_type != "other":
            lines.append((2, f"🏷 {esc(type_fa)}"))
        if listing.price_max:
            lines.append((1, f"💰 بودجه تا <b>{esc(money(listing.price_max, listing.price_currency))}</b>"))
    else:
        lines.append((0, f"🏠 <b>{esc(TYPE_FA.get(listing.property_type, 'ملک'))} · {esc(place)}</b>"))
        if listing.price_amount:
            extra = []
            period = PERIOD_FA.get(listing.price_period, "")
            if period:
                extra.append(period)
            if listing.price_basis in BASIS_FA:
                extra.append(BASIS_FA[listing.price_basis])
            elif listing.bills_included == "yes":
                extra.append("قبوض داخل قیمت")
            elif listing.bills_included == "no":
                extra.append("قبوض جدا")
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
    if listing.furnished in FURNISHED_FA:
        facts.append(FURNISHED_FA[listing.furnished])
    if listing.registration == "yes":
        facts.append("ملد ✅")
    elif listing.registration == "no":
        facts.append("بدون ملد")
    if facts:
        lines.append((1, " · ".join(esc(item) for item in facts)))
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
    warning = REGION_WARNING_FA.get(listing.region) if (listing.flags or {}).get("deposit_warning") else ""
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
    if listing.pk:
        # The link stays in the text. The glass button is attached separately, because a startapp link in the text does not open.
        lines.append((0, f'🔗 <a href="{esc(conf.mini_app_link(listing.pk))}">دیدن آگهی و تماس در چمدون خانه</a>'))
    hashtags = " ".join(
        item
        for item in (
            tag(listing.city),
            tag(TYPE_FA.get(listing.property_type, "")) if listing.kind == ListingKind.OFFER else "#دنبال_خانه",
        )
        if item
    )
    if hashtags:
        lines.append((3, hashtags))
    return lines


def format_listing_text(listing: HomeListing, *, limit: int = MESSAGE_LIMIT, phone: bool | None = None) -> str:
    """The Persian post for a listing. Over the limit, the lowest-priority lines go first."""
    if phone is None:
        phone = conf.publish_phone()
    lines = listing_lines(listing, phone=phone)
    while True:
        text = "\n".join(line for _priority, line in lines)
        if len(text) <= limit or len(lines) <= 1:
            return text[:limit]
        worst = max(priority for priority, _ in lines)
        for index in range(len(lines) - 1, -1, -1):
            if lines[index][0] == worst:
                del lines[index]
                break


def photo_urls(listing: HomeListing) -> list[str]:
    storage = get_storage()
    if storage is None:
        return []
    keys = listing.post.media.filter(status=MediaStatus.STORED).order_by("position").values_list("storage_key", flat=True)
    return [storage.url(key) for key in keys[:10] if key]


def waiting_for_photos(listing: HomeListing) -> bool:
    """Photos are still being fetched; hold the post so the album goes out whole."""
    return get_storage() is not None and listing.post.media.filter(status=MediaStatus.PENDING).exists()


def publishable():
    window = conf.publish_window_days()
    channel_countries = HomeChannel.objects.filter(enabled=True).values_list("country", flat=True)
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
        rows.append(
            {
                "id": listing.pk,
                "country": listing.country,
                "text": format_listing_text(listing, limit=CAPTION_LIMIT),
                "photos": len(photo_urls(listing)),
                "waiting_for_photos": waiting_for_photos(listing),
            }
        )
    return rows


def _posted_last_day(channel: HomeChannel) -> int:
    return HomeListing.objects.filter(channel=channel, published_at__gte=timezone.now() - timedelta(days=1)).count()


def _app_button(listing: HomeListing) -> dict:
    return {"text": "دیدن در چمدون خانه", "url": conf.mini_app_link(listing.pk)}


def publish_listing(listing: HomeListing, channel: HomeChannel) -> list[int]:
    """Send one listing. Returns the message ids. Raises BotError."""
    if is_courier_channel(channel.username, channel.chat_id):
        raise bot.BotError("This is the courier channel. Housing never posts there.")
    target = channel.target()
    urls = photo_urls(listing)
    note = ""
    ids: list[int] = []
    button = _app_button(listing)
    if urls:
        caption = format_listing_text(listing, limit=CAPTION_LIMIT)
        try:
            if len(urls) == 1:
                ids = bot.send_photo(target, urls[0], caption, button=button)
            else:
                # Albums cannot carry a button, so the same glass button goes out as a reply under the album.
                ids = bot.send_album(target, urls, caption)
                try:
                    ids.extend(bot.send_message(target, button["text"], button=button, reply_to=ids[0]))
                except bot.BotError as exc:
                    note = f"button skipped: {exc.description}"[:300]
        except bot.BotError as exc:
            if exc.retry_after or exc.code in {401, 403, 429}:
                raise
            note = f"photos skipped: {exc.description}"[:300]
    if not ids:
        ids = bot.send_message(target, format_listing_text(listing), button=button)
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


def publish_pending(*, limit: int | None = None, stop_at: float | None = None) -> dict:
    """Post the next listings, a few per run. Never raises on Telegram trouble."""
    if not conf.publish_ready():
        return {"ok": True, "skipped": conf.publish_blocker(), "published": 0}
    limit = limit or conf.publish_batch()
    channels = {channel.country: channel for channel in HomeChannel.objects.filter(enabled=True)}
    posted_today = {country: _posted_last_day(channel) for country, channel in channels.items()}
    cap = conf.publish_daily_cap()
    published = failed = held = capped = 0
    retry_after = 0
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
    return {
        "ok": True,
        "published": published,
        "failed": failed,
        "held_for_photos": held,
        "over_daily_cap": capped,
        "retry_after": retry_after,
        "remaining": publishable().count(),
    }


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
