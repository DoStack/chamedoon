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
import re
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
from home.places import FA_TO_LATIN, fold, split_areas, toronto_district
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
# Short type words for the post title and its hashtag («اتاق مبله», «#اتاق»). A German room is a WG room.
TYPE_SHORT_FA = {
    "room": "اتاق",
    "studio": "استودیو",
    "apartment": "آپارتمان",
    "house": "خانه",
    "basement": "بیسمنت",
    "sublet": "سابلت",
    "lease_transfer": "انتقال لیز",
    "short_stay": "اقامت کوتاه‌مدت",
    "other": "",
}
# Bidi marks keep Latin bits in place inside a right-to-left line: «۹۵۰ CA$» (not «$CA ۹۵۰»), «@user» (not «user@»),
# «اتاق WG ۱۶ متری» (not «اتاق ۱۶ WG متری») and «#اتاق_WG #زیر_۷۰۰». Telegram ends a hashtag or mention at a mark.
LRM = "‎"
RLM = "‏"
TYPE_SHORT_BY_COUNTRY = {"DE": {"room": f"اتاق WG{RLM}"}}
PERIOD_FA = {"month": "در ماه", "week": "در هفته", "night": "هر شب"}
FURNISHED_FA = {"yes": "مبله", "partial": "نیمه‌مبله"}
# Fixed rent bands per currency for the «#زیر_<band>» hashtag; above the top band: «#بالای_<top>».
PRICE_BANDS = {"CAD": (800, 1000, 1500, 2000, 3000), "EUR": (500, 700, 1000, 1500, 2000)}
# A channel post has at most this many lines, then the hashtags (tone guide: max 8 lines + hashtags).
MAX_BODY_LINES = 8
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
CONDITION_FLAGS = (
    ("first_last", "ماه اول و آخر"),
    ("credit_check", "کردیت چک"),
    ("wgb", "WBS لازم"),
)
# Toronto street names in places.FA_TO_LATIN: an intersection stays in English («Finch و Bathurst»).
STREET_NAMES = {"yonge", "finch", "sheppard", "steeles", "bayview"}
# The first Persian spelling of each district or suburb, for the post and its hashtag.
PLACE_FA = {}
for _persian, _latin in FA_TO_LATIN.items():
    if _latin not in STREET_NAMES:
        PLACE_FA.setdefault(_latin, _persian)
PERSIAN_LETTERS = re.compile(r"[؀-ۿ]")
DETAILS_LABEL = "جزئیات و تماس"
DETAILS_BUTTON = f"📷 {DETAILS_LABEL}"
SIMILAR_BUTTON = "🏘 مشابه‌ها"
ALERT_BUTTON = "🔔 آگهی‌های این‌شکلی رو خبرم کن"


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


def money_fa(amount: Decimal | None, currency: str) -> str:
    """Money as the channel writes it: Persian digits, «٬» between thousands, the unit after the number (۱٬۲۰۰ CA$)."""
    if amount is None:
        return ""
    number = f"{amount:,.0f}" if amount == amount.to_integral_value() else f"{amount:,.2f}"
    number = fa(number.replace(",", "٬").replace(".", "٫"))
    symbol = CURRENCY_SYMBOL.get(currency, currency)
    if re.search(r"[A-Za-z]", symbol):
        symbol += LRM
    return f"{number} {symbol}".strip()


def short_date(value, today=None) -> str:
    """«۱ دسامبر»; the year only when it is not this year."""
    today = today or timezone.localdate()
    text = f"{fa(value.day)} {MONTHS[value.month - 1]}"
    return f"{text} {fa(value.year)}" if value.year != today.year else text


def tag(text: str) -> str:
    """A Telegram hashtag: letters, digits and _ only. Spaces, ZWNJ and punctuation become one _."""
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in (text or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
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


def place_fa(name: str) -> str:
    """A district or suburb in Persian when its usual spelling is known; otherwise as written."""
    name = (name or "").strip()
    if not name or PERSIAN_LETTERS.search(name):
        return name
    known = {**PLACE_FA, **CITY_FA}
    key = fold(name)
    if key in known:
        return known[key]
    # Several places («North York, Thornhill»): Persian only when every one is known; an intersection stays as written.
    parts = [fold(part) for part in split_areas(name)]
    if len(parts) > 1 and all(part in known for part in parts):
        return " یا ".join(known[part] for part in parts)
    return name


def place_line(listing: HomeListing, metro: str) -> str:
    """«📍 محله، شهر»: the area first, then the suburb when it is not the metro, then the metro, in Persian."""
    metro = metro or listing.city or ""
    parts, seen = [], set()
    for name, shown in (
        (listing.area, place_fa(listing.area)),
        (listing.city, place_fa(listing.city)),
        (metro, city_fa(metro)),
    ):
        key = fold(name or "")
        if key and key not in seen:
            seen.add(key)
            parts.append(esc(shown))
    return "📍 " + "، ".join(parts) if parts else ""


def area_tag(listing: HomeListing, metro: str) -> str:
    """The neighbourhood hashtag: the first named area (a Toronto intersection: its district), else the suburb."""
    metro_key = fold(metro or listing.city or "")
    areas = [name for name in split_areas(listing.area) if fold(name) and fold(name) != metro_key]
    first = areas[0] if areas else ""
    if first:
        named = place_fa(first)
        if named != first or PERSIAN_LETTERS.search(first):
            return tag(named)
        district = toronto_district(first) if metro_key == "toronto" else ""
        if district:
            return tag(PLACE_FA.get(district, district))
    if listing.city and fold(listing.city) != metro_key:
        return tag(place_fa(listing.city))
    return tag(first) if first and len(first) <= 24 else ""


def type_word(listing: HomeListing) -> str:
    """The short type («اتاق», «اتاق WG» in Germany, «آپارتمان»), or blank for other."""
    local = TYPE_SHORT_BY_COUNTRY.get(listing.country, {})
    return local.get(listing.property_type) or TYPE_SHORT_FA.get(listing.property_type, "")


def title(listing: HomeListing) -> str:
    """The short title of an offer: type, bedrooms, size and furnished («آپارتمان ۲ خوابه ۶۰ متری مبله»)."""
    parts = [type_word(listing) or "ملک"]
    if listing.rooms and listing.property_type in {"apartment", "house", "basement"}:
        parts.append(f"{fa(num(listing.rooms))} {'اتاقه' if listing.country == 'DE' else 'خوابه'}")
    if listing.size_m2:
        parts.append(f"{fa(num(listing.size_m2))} متری")
    if listing.furnished in FURNISHED_FA:
        parts.append(FURNISHED_FA[listing.furnished])
    return " ".join(parts)


def rent_basis(listing: HomeListing) -> str:
    """Germany always says warm or cold; elsewhere only that the bills are extra."""
    if listing.country == "DE":
        if listing.price_basis == "warm" or (listing.price_basis != "cold" and listing.bills_included == "yes"):
            return "گرم"
        if listing.price_basis == "cold" or listing.bills_included == "no":
            return "سرد"
        return "گرم/سرد نامشخص"
    if listing.price_basis == "cold" or listing.bills_included == "no":
        return "قبوض جدا"
    return ""


def head_line(listing: HomeListing) -> str:
    """Line 1: the price and the short title, or for a wanted post the search and the budget."""
    if listing.kind == ListingKind.WANTED:
        parts = ["🔎 <b>دنبال خونه</b>"]
        if type_word(listing):
            parts.append(f"<b>{esc(type_word(listing))}</b>")
        if listing.price_max:
            parts.append(f"بودجه تا <b>{esc(money_fa(listing.price_max, listing.price_currency))}</b>")
        return " · ".join(parts)
    if listing.price_amount:
        extra = "، ".join(item for item in (PERIOD_FA.get(listing.price_period, ""), rent_basis(listing)) if item)
        price = f"💶 <b>{esc(money_fa(listing.price_amount, listing.price_currency))}</b>" + (f" {esc(extra)}" if extra else "")
    else:
        price = "💶 قیمت: بپرسید"
    return f"{price} · <b>{esc(title(listing))}</b>"


def advantages(listing: HomeListing) -> list[str]:
    """The ✅ line: what a renter is glad to read (bills, Anmeldung, parking, pets, no agent)."""
    if listing.kind != ListingKind.OFFER:
        return []
    flags = listing.flags or {}
    items = []
    if listing.bills_included == "yes" and rent_basis(listing) != "گرم":
        items.append("قبوض داخل")
    if listing.registration == "yes":
        items.append("ملد (Anmeldung) دارد")
    if flags.get("parking") is True:
        items.append("پارکینگ")
    if flags.get("pets_ok") is True:
        items.append("حیوان خانگی مجاز")
    if listing.lister_type == ListerType.OWNER:
        items.append("بدون بنگاه")
    return items


def date_items(listing: HomeListing) -> list[str]:
    items = []
    if listing.available_from:
        items.append(f"از {short_date(listing.available_from)}")
    if listing.available_to:
        items.append(f"تا {short_date(listing.available_to)}")
    if listing.min_term_months:
        items.append(f"حداقل {fa(listing.min_term_months)} ماه")
    kind = TERM_FA.get(term(listing), "")
    if kind and kind != type_word(listing):
        items.append(kind)
    return items


def roommate_items(listing: HomeListing) -> list[str]:
    flags = listing.flags or {}
    items = []
    if listing.roommate_gender in GENDER_FA:
        items.append(GENDER_FA[listing.roommate_gender])
    if listing.kind == ListingKind.WANTED and listing.occupants_wanted:
        items.append(f"{fa(listing.occupants_wanted)} نفر")
    if flags.get("students_only") is True:
        items.append("فقط دانشجو")
    if flags.get("couples_ok") is True and listing.roommate_gender != "couple":
        items.append("مناسب زوج")
    if flags.get("no_smoking") is True:
        items.append("سیگار ممنوع")
    if flags.get("pets_ok") is False:
        items.append("بدون حیوان خانگی")
    return items


def warning_items(listing: HomeListing) -> list[str]:
    """The ⚠️ line, only when there is something to know before calling: agent, no Anmeldung, deposit, conditions."""
    flags = listing.flags or {}
    items = []
    if listing.kind == ListingKind.OFFER and listing.lister_type == ListerType.AGENT:
        items.append("بنگاه یا آگهی‌دهنده‌ی پرتکرار")
    if listing.registration == "no":
        items.append("بدون ملد")
    if listing.deposit_amount:
        items.append(f"ودیعه {money_fa(listing.deposit_amount, listing.price_currency)}")
    elif listing.deposit_months:
        items.append(f"ودیعه {fa(num(listing.deposit_months))} ماه")
    items += [label for key, label in CONDITION_FLAGS if flags.get(key)]
    warning = REGION_WARNING_FA.get(listing.region) if flags.get("deposit_warning") else ""
    if warning:
        items.append(warning)
    return items


def contact_line(listing: HomeListing, *, phone: bool) -> str:
    """The contact as the advertiser gave it (Telegram, phone) and the link to the original post."""
    items = []
    if listing.contact_telegram:
        items.append(f"{LRM}@{esc(listing.contact_telegram)}")
    if phone and listing.contact_phone:
        items.append(f"<code>{esc(listing.contact_phone)}</code>")
    post = listing.post
    link = post.source_url if post.source_url and "/c/" not in post.source_url else ""
    if items:
        if link:
            items.append(f'<a href="{esc(link)}">پست اصلی</a>')
        return "📞 تماس: " + " · ".join(items)
    if link:
        return f'📞 تماس: در <a href="{esc(link)}">پست اصلی</a>'
    return "📞 تماس: از طریق ربات خانه (به‌زودی)"


def band_tag(listing: HomeListing) -> str:
    """«#زیر_۱۰۰۰» from the fixed monthly bands of the currency; above the top band «#بالای_۳۰۰۰»."""
    bands = PRICE_BANDS.get(listing.price_currency)
    amount = listing.price_amount
    if not bands or not amount or listing.price_period != "month":
        return ""
    for band in bands:
        if amount <= band:
            return f"#زیر_{fa(band)}"
    return f"#بالای_{fa(bands[-1])}"


def hashtags(listing: HomeListing, metro: str) -> str:
    """«#شهر #محله #نوع #زیر_<band>» (and «#ملد_دارد» in Germany); a wanted post: «#شهر #دنبال_خانه #نوع»."""
    city = tag(city_fa(metro or listing.city))
    kind = tag(type_word(listing))
    if listing.kind == ListingKind.WANTED:
        items = [city, "#دنبال_خانه", kind]
    else:
        items = [city, area_tag(listing, metro), kind, band_tag(listing)]
        if listing.country == "DE" and listing.registration == "yes":
            items.append("#ملد_دارد")
    unique = []
    for item in items:
        if item and item not in unique:
            unique.append(item)
    return " ".join(item + RLM for item in unique)


def listing_lines(listing: HomeListing, *, phone: bool, metro: str = "", photos: int = 0) -> list[tuple[int, str]]:
    """Lines of a post with a priority; higher numbers are dropped first when a caption is too long.

    Marketplace style, one fixed emoji per line: price and title, place, the biggest advantages,
    dates and term, roommate rules, a warning only when needed, contact, then the hashtags.
    The AI summary is not in the channel post; the Mini App detail page shows it.
    """
    lines: list[tuple[int, str]] = [(0, head_line(listing))]
    place = place_line(listing, metro)
    if place:
        lines.append((0, place))
    for priority, emoji, items in (
        (1, "✅", advantages(listing)),
        (1, "📅", date_items(listing)),
        (2, "👤", roommate_items(listing)),
        (3, "⚠️", warning_items(listing)),
    ):
        if items:
            lines.append((priority, f"{emoji} " + " · ".join(esc(item) for item in items)))
    lines.append((0, contact_line(listing, phone=phone)))
    if photos > 1:
        # A text post whose photos Telegram refused: the photos are still one tap away, in the Mini App.
        lines.append((1, f"📷 {fa(photos)} عکس در «{DETAILS_LABEL}»"))
    while len(lines) > MAX_BODY_LINES and drop(lines):
        pass
    tags = hashtags(listing, metro or listing.city)
    if tags:
        lines.append((2, tags))
    return lines


def drop(lines: list[tuple[int, str]]) -> bool:
    """Remove the last line of the lowest priority. Lines of priority 0 (price, title, place, contact) stay."""
    worst = max((priority for priority, _ in lines), default=0)
    if worst == 0:
        return False
    for index in range(len(lines) - 1, -1, -1):
        if lines[index][0] == worst:
            del lines[index]
            return True
    return False


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
        if len(text) <= limit or not drop(lines):
            return text[:limit]


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
    """The keyboard under a post: details and contact (the listing), similar (city + type + rent band), city alerts."""
    metro = metro_of(listing, channel) if metro is None else metro
    source = deeplink.source_tag(channel.country, channel.city)
    property_type = listing.property_type if listing.property_type != "other" else ""
    details = deeplink.build(listing.country, metro, listing=listing.pk, source=source)
    similar = deeplink.build(listing.country, metro, type=property_type, band=price_band(listing), source=source)
    return [
        [
            {"text": DETAILS_BUTTON, "url": conf.mini_app_link(start=details)},
            {"text": SIMILAR_BUTTON, "url": conf.mini_app_link(start=similar)},
        ],
        [{"text": ALERT_BUTTON, "url": conf.bot_start_link(deeplink.alert_start(listing.country, metro))}],
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
                    ids.extend(bot.send_message(target, f"👆 {DETAILS_LABEL}", buttons=buttons, reply_to=ids[0]))
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
    blocker = conf.publish_blocker()
    if blocker:
        if blocker == conf.BOT_TOKEN_MISSING:
            logger.error("housing publish skipped: %s", blocker)
        return {"ok": True, "skipped": blocker, "published": 0}
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
