"""The channel post format v2 (Channel2 boards), behind `HomeSettings.channel_v2`.

Price first, then type and neighbourhood, a blank line, the details in a quote, a blank line, the hashtags.
No contact and no source link: both live behind «📞 جزئیات و تماس».

The pinned post, the group welcome and the weekly report are built here too, but nothing here sends them.
The manage.py command `home_channel_post` does, only with --apply: each one needs Mohammad's yes (Track C).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from statistics import median

from django.utils import timezone

from home import conf, deeplink
from home.contact_text import safe_area, safe_city
from home.models import HomeChannel, HomeListing, ListingKind, ListingStatus
from home.places import fold, split_areas, toronto_district
from home.publish import (
    FURNISHED_FA,
    MESSAGE_LIMIT,
    PERIOD_FA,
    PERSIAN_LETTERS,
    PLACE_FA,
    RLM,
    city_channels,
    city_fa,
    esc,
    fa,
    metro_of,
    money_fa,
    num,
    place_fa,
    price_band,
    rent_basis,
    roommate_items,
    route,
    short_date,
    tag,
    term,
    type_word,
    warning_items,
)

DETAILS_LABEL = "جزئیات و تماس"
DETAILS_BUTTON = f"📞 {DETAILS_LABEL}"
SIMILAR_BUTTON = "🏘 مشابه‌ها"
ALERT_BUTTON = "🔔 گوش به زنگ مشابه"
OWNER_BUTTON = "🏠 خانه دارم، معرفی کنم"
POST_BUTTON = "➕ ثبت آگهی"
CLOSED_LINE = "🚫 <b>بسته شد</b>"
SEARCH_BUTTON = "🔎 جستجو در ربات"
BOT_ALERT_BUTTON = "🔔 گوش به زنگ"
OPEN_APP_BUTTON = "🏠 باز کردن مینی‌اپ"
# The bands of the pinned post. Germany's are warm rent.
BANDS = {"CAD": (800, 1000, 1500, 2000, 3000), "EUR": (500, 700, 900, 1200)}
SUBLET_WORD = {"CA": {"sublet": "سابلت", "lease_transfer": "انتقال لیز", "short_term": "کوتاه‌مدت"}}
SUBLET_WORD["DE"] = {"sublet": "Zwischenmiete", "lease_transfer": "Nachmieter", "short_term": "کوتاه‌مدت"}
# The weekly report's three rows: the label, the property types, the number of rooms (None for a room).
REPORT_ROWS = (("اتاق", {"room"}, None), ("۱ خوابه", {"apartment", "basement", "house", "studio"}, 1), ("۲ خوابه", {"apartment", "basement", "house"}, 2))
REPORT_DAYS = 7


def rooms_word(listing: HomeListing) -> str:
    if not listing.rooms or listing.property_type not in {"apartment", "house", "basement"}:
        return ""
    return f"{fa(num(listing.rooms))} {'اتاقه' if listing.country == 'DE' else 'خوابه'}"


def district(listing: HomeListing, metro: str) -> str:
    """The neighbourhood of the title line: named area, an intersection's district, the suburb, else the metro."""
    city = safe_city(listing)
    metro_key = fold(metro or city or "")
    areas = [name for name in split_areas(safe_area(listing)) if fold(name) and fold(name) != metro_key]
    if areas:
        names = [_named_area(name, metro_key) for name in areas[:2]]
        return " یا ".join(dict.fromkeys(names))
    if city and fold(city) != metro_key:
        return place_fa(city)
    return city_fa(metro or city)


def _named_area(name: str, metro_key: str) -> str:
    named = place_fa(name)
    if named != name or PERSIAN_LETTERS.search(name):
        return named
    found = toronto_district(name) if metro_key == "toronto" else ""
    return PLACE_FA.get(found, found) if found else name


def near_line(listing: HomeListing, metro: str) -> str:
    """«📍 نزدیک Yonge و Finch» when the area says more than the neighbourhood in the title."""
    area = safe_area(listing).strip()
    if not area or district(listing, metro) == place_fa(area) or any(PERSIAN_LETTERS.search(part) for part in split_areas(area)):
        return ""
    return f"📍 نزدیک {esc(area.replace(' & ', ' و '))}"


def head_lines(listing: HomeListing, metro: str) -> list[str]:
    place = esc(district(listing, metro))
    kind = " ".join(part for part in (type_word(listing) or "ملک", rooms_word(listing)) if part)
    second = f"🏠 <b>{esc(kind)}</b> · {place}" if place else f"🏠 <b>{esc(kind)}</b>"
    if listing.kind == ListingKind.WANTED:
        budget = f" · بودجه تا <b>{esc(money_fa(listing.price_max, listing.price_currency))}</b>" if listing.price_max else ""
        return [f"🔎 <b>دنبال خانه</b>{budget}", second]
    if not listing.price_amount:
        return ["💶 <b>قیمت: بپرسید</b>", second]
    period = PERIOD_FA.get(listing.price_period, "")
    basis = rent_basis(listing)
    tail = " · ".join(item for item in (period, basis) if item)
    first = f"💶 <b>{esc(money_fa(listing.price_amount, listing.price_currency))}</b>" + (f" {esc(tail)}" if tail else "")
    return [first, second]


def mode_line(listing: HomeListing) -> str:
    """«🔁 <b>سابلت</b> · ۱ دسامبر تا ۲۸ فوریه»: only for a rental that is not the usual long lease."""
    word = SUBLET_WORD.get(listing.country, SUBLET_WORD["CA"]).get(term(listing))
    if not word:
        return ""
    dates = []
    if listing.available_from:
        dates.append(short_date(listing.available_from))
    if listing.available_to:
        dates.append(short_date(listing.available_to))
    span = " تا ".join(dates)
    return f"🔁 <b>{esc(word)}</b>" + (f" · {esc(span)}" if span else "")


def comfort_items(listing: HomeListing) -> list[str]:
    items = []
    if listing.furnished in FURNISHED_FA:
        items.append(FURNISHED_FA[listing.furnished])
    if listing.size_m2:
        items.append(f"{fa(num(listing.size_m2))} متری")
    if listing.bills_included == "yes" and rent_basis(listing) != "گرم":
        items.append("قبوض داخل")
    flags = listing.flags or {}
    if flags.get("parking") is True:
        items.append("پارکینگ")
    if flags.get("pets_ok") is True:
        items.append("حیوان خانگی مجاز")
    return items


def date_line(listing: HomeListing, mode: str) -> str:
    items = []
    if listing.available_from and not mode:
        items.append(f"از {short_date(listing.available_from)}")
    if listing.available_to and not mode:
        items.append(f"تا {short_date(listing.available_to)}")
    if listing.min_term_months:
        items.append(f"حداقل {fa(listing.min_term_months)} ماه")
    return "📅 " + " · ".join(esc(item) for item in items) if items else ""


def quote_lines(listing: HomeListing, metro: str, photos: int = 0) -> list[tuple[int, str]]:
    """The quote, each line with a priority: higher numbers go first when a caption is too long."""
    mode = mode_line(listing)
    lines: list[tuple[int, str]] = []
    if mode:
        lines.append((0, mode))
    near = near_line(listing, metro)
    if near:
        lines.append((3, near))
    comfort = comfort_items(listing) if listing.kind == ListingKind.OFFER else []
    if comfort:
        lines.append((2, "🛋 " + " · ".join(esc(item) for item in comfort)))
    dates = date_line(listing, mode)
    if dates:
        lines.append((1, dates))
    if listing.country == "DE" and listing.registration == "yes":
        lines.append((1, "✅ ملد (Anmeldung) ممکن"))
    people = roommate_items(listing)
    if people:
        lines.append((2, "👤 " + " · ".join(esc(item) for item in people)))
    warnings = warning_items(listing)
    if warnings:
        lines.append((4, "⚠️ " + " · ".join(esc(item) for item in warnings)))
    if photos > 1:
        # A text post whose photos Telegram refused: the photos are still one tap away, in the Mini App.
        lines.append((1, f"📷 {fa(photos)} عکس در «{DETAILS_LABEL}»"))
    return lines


def band_tag(listing: HomeListing) -> str:
    bands = BANDS.get(listing.price_currency)
    amount = listing.price_max if listing.kind == ListingKind.WANTED else listing.price_amount
    if not bands or not amount or listing.price_period != "month":
        return ""
    for band in bands:
        if amount <= band:
            return f"#زیر_{fa(band)}"
    return f"#بالای_{fa(bands[-1])}"


def type_tag(listing: HomeListing) -> str:
    """«#یک_خوابه» for one bedroom, as the board spells it; otherwise the type or its bedrooms."""
    if listing.rooms == 1 and rooms_word(listing):
        return "#یک_خوابه"
    return tag(f"{fa(num(listing.rooms))} خوابه") if rooms_word(listing) and listing.country != "DE" else tag(type_word(listing))


def hashtags(listing: HomeListing, metro: str) -> str:
    place = district(listing, metro)
    city = tag(city_fa(metro or safe_city(listing)))
    area = tag(place) if place != city_fa(metro or safe_city(listing)) and " یا " not in place and len(place) <= 24 else ""
    if listing.kind == ListingKind.WANTED and " یا " in place:
        area = tag(place.split(" یا ")[0])
    mode = tag(SUBLET_WORD.get(listing.country, SUBLET_WORD["CA"]).get(term(listing), ""))
    if listing.kind == ListingKind.WANTED:
        items = [city, "#دنبال_خانه", area, type_tag(listing), mode, band_tag(listing)]
    else:
        items = [city, area, type_tag(listing), mode, band_tag(listing)]
        if listing.country == "DE" and listing.registration == "yes":
            items.append("#ملد_دارد")
    return " ".join(item + RLM for item in dict.fromkeys(item for item in items if item))


def post_text(listing: HomeListing, *, limit: int = MESSAGE_LIMIT, metro: str | None = None, photos: int = 0) -> str:
    """The post. Over the limit, whole lines go, lowest priority first, so an HTML tag is never cut."""
    metro = metro_of(listing) if metro is None else metro
    head = head_lines(listing, metro)
    quote = quote_lines(listing, metro, photos)
    tags = hashtags(listing, metro)

    def join() -> str:
        parts = ["\n".join(head)]
        if quote:
            parts.append("<blockquote>" + "\n".join(line for _priority, line in quote) + "</blockquote>")
        if tags:
            parts.append(tags)
        return "\n\n".join(parts)

    text = join()
    while len(text) > limit and quote:
        worst = max(priority for priority, _line in quote)
        last = max(index for index, (priority, _line) in enumerate(quote) if priority == worst)
        del quote[last]
        text = join()
    if len(text) > limit:
        tags = ""
        text = join()
    return text if len(text) <= limit else "\n".join(head)


def post_buttons(listing: HomeListing, channel: HomeChannel, metro: str | None = None) -> list[list[dict]]:
    metro = metro_of(listing, channel) if metro is None else metro
    source = deeplink.source_tag(channel.country, channel.city)
    details = conf.mini_app_link(start=deeplink.build(listing.country, metro, listing=listing.pk, source=source))
    if listing.kind == ListingKind.WANTED:
        home = conf.mini_app_link(start=deeplink.build(listing.country, metro, source=source))
        return [[{"text": OWNER_BUTTON, "url": details}], [{"text": POST_BUTTON, "url": home}]]
    property_type = listing.property_type if listing.property_type != "other" else ""
    similar = deeplink.build(listing.country, metro, type=property_type, band=price_band(listing), source=source)
    return [
        [{"text": DETAILS_BUTTON, "url": details}, {"text": SIMILAR_BUTTON, "url": conf.mini_app_link(start=similar)}],
        [{"text": ALERT_BUTTON, "url": conf.bot_start_link(deeplink.alert_start(listing.country, metro))}],
    ]


def closed_text(listing: HomeListing) -> str:
    """The edit of a post that closed: «🚫 بسته شد», the title struck through, the day."""
    metro = metro_of(listing)
    kind = " ".join(part for part in (type_word(listing) or "ملک", rooms_word(listing)) if part)
    place = district(listing, metro)
    title = f"🏠 {esc(kind)}" + (f" · {esc(place)}" if place else "")
    when = short_date(timezone.localtime(listing.rented_at or timezone.now()))
    return f"{CLOSED_LINE}\n<s>{title}</s>\n\n{esc(when)}"


# ---------------------------------------------------------------------------
# Posts that are not listings. Built here, sent only by `home_channel_post --apply`.


def _title(channel: HomeChannel) -> str:
    return f"چمدون خانه · {city_fa(channel.city)}" if channel.city else "چمدون خانه"


def _currency(channel: HomeChannel) -> str:
    return "EUR" if channel.country == "DE" else "CAD"


def _bot_buttons(channel: HomeChannel) -> tuple[dict, dict]:
    start = deeplink.alert_start(channel.country, channel.city)
    return {"text": SEARCH_BUTTON, "url": conf.bot_start_link("search")}, {"text": BOT_ALERT_BUTTON, "url": conf.bot_start_link(start)}


def pinned_text(channel: HomeChannel) -> str:
    bands = BANDS[_currency(channel)]
    tags = " ".join(f"#زیر_{fa(band)}{RLM}" for band in bands) + f" #بالای_{fa(bands[-1])}{RLM}"
    german = channel.country == "DE"
    price = "💶 قیمت اول؛ گرم یا سرد همیشه نوشته می‌شود" if german else "💶 قیمت اول، بعد نوع خانه و محله"
    safety = "⚠️ قبل از دیدن خانه و امضای قرارداد پولی نفرستید." if german else "⚠️ قبل از دیدن خانه پولی نفرستید."
    label = "هشتگ‌های قیمت (اجاره‌ی گرم) برای جستجو:" if german else "هشتگ‌های قیمت برای جستجو:"
    return (
        f"📌 <b>{esc(_title(channel))}</b>\n"
        "آگهی‌های خانه‌ی گروه‌های فارسی شهر، مرتب و بدون تکرار.\n\n"
        f"<blockquote>🔎 جستجو و 🔔 گوش به زنگ در ربات\n{price}\n{safety}</blockquote>\n\n"
        f"{label}\n\n{tags}"
    )


def pinned_buttons(channel: HomeChannel) -> list[list[dict]]:
    search, alert = _bot_buttons(channel)
    home = conf.mini_app_link(start=deeplink.build(channel.country, channel.city or "", source=deeplink.source_tag(channel.country, channel.city)))
    return [[search, alert], [{"text": OPEN_APP_BUTTON, "url": home}]]


def welcome_text(first_name: str, channel: HomeChannel) -> str:
    hello = f"سلام {esc(first_name)} 👋" if first_name else "سلام 👋"
    return (
        f"{hello} به گروه <b>{esc(_title(channel))}</b> خوش آمدید.\n"
        "اینجا آگهی خانه می‌گذارید یا می‌پرسید.\n"
        "قبل از دیدن خانه پولی نفرستید.\n"
        "جستجو و گوش به زنگ در ربات است."
    )


def welcome_buttons(channel: HomeChannel) -> list[list[dict]]:
    search, _alert = _bot_buttons(channel)
    return [[search, {"text": POST_BUTTON, "url": conf.mini_app_link()}]]


def _week_rows(channel: HomeChannel, since: datetime) -> list[HomeListing]:
    """Offers first seen this week that route to this city channel; a country channel takes the whole country."""
    rows = HomeListing.objects.filter(country=channel.country, kind=ListingKind.OFFER, first_seen__gte=since).exclude(status=ListingStatus.DUPLICATE)
    if not channel.city:
        return list(rows)
    channels = city_channels()
    return [listing for listing in rows if (found := route(listing, channels)) is not None and found.pk == channel.pk]


def weekly_report(channel: HomeChannel, now: datetime | None = None) -> dict:
    """The numbers of the weekly report. A median needs prices, so it only counts monthly rents that state one."""
    now = now or timezone.now()
    since = now - timedelta(days=REPORT_DAYS)
    rows = _week_rows(channel, since)
    priced = [listing for listing in rows if listing.price_amount and listing.price_period == "month"]
    medians = []
    for label, types, rooms in REPORT_ROWS:
        prices = [
            listing.price_amount
            for listing in priced
            if listing.property_type in types and (rooms is None or listing.rooms == Decimal(rooms) or (rooms == 1 and listing.property_type == "studio"))
        ]
        medians.append({"label": label, "count": len(prices), "median": median(prices) if prices else None})
    closed = HomeListing.objects.filter(country=channel.country, status=ListingStatus.RENTED, rented_at__gte=since)
    channels = city_channels()
    closed_rows = list(closed) if not channel.city else [item for item in closed if (found := route(item, channels)) is not None and found.pk == channel.pk]
    return {"new": len(rows), "priced": len(priced), "rows": medians, "closed": len(closed_rows)}


def weekly_text(channel: HomeChannel, now: datetime | None = None) -> str:
    report = weekly_report(channel, now)
    currency = _currency(channel)
    lines = [
        f"🏠 {esc(row['label'])}: میانه‌ی قیمت {esc(money_fa(row['median'], currency))} (از {fa(row['count'])} آگهی)"
        for row in report["rows"]
        if row["median"] is not None
    ]
    lines.append(f"🚫 بسته شد: {fa(report['closed'])}")
    name = city_fa(channel.city) if channel.city else "همه"
    return (
        f"📊 <b>گزارش هفته · {esc(name)}</b>\n"
        f"آگهی تازه: {fa(report['new'])} · آگهی با قیمت: {fa(report['priced'])}\n\n"
        "<blockquote>" + "\n".join(lines) + "</blockquote>\n\n"
        "میانه فقط از آگهی‌های دارای قیمت حساب شده است."
    )


def weekly_buttons(channel: HomeChannel) -> list[list[dict]]:
    return [list(_bot_buttons(channel))]

