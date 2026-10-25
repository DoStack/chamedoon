"""«گوش به زنگ»: match new listings to saved alerts and message each user once per listing.

An alert is made in the housing bot (home/botchat.py: three questions, type, budget and
area, plus optional ones) or copied from the user's own active "wanted" post in the Mini
App, so a saved request is notified the same way. A listing matches when it is an offer
in the alert's metro (the city channel's aliases bring the suburbs in, through the same
`route()` the channel posting uses), under the budget, of the wanted type and term, and in
the neighbourhood if one was given.

Sending is a job step (`alerts`) with its own switch in Home settings, off by default.
The job also runs it right after the review step, so a listing accepted in a run is
messaged in that same run (see home/job.py). A user hears about a listing once, at most
`alerts_daily_cap` messages a day; more than `SUMMARY_AT` matches at once go out as one
summary message. Nothing is sent from 23:00 to 08:00 in the city's own time: those
matches wait and go out with the first run after 08:00. A paused alert («یک هفته مکث»)
is skipped until `paused_until`; a user who blocked the bot has their alerts switched off.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db.models import Q
from django.utils import timezone

from home import bot, conf
from home.places import area_matches
from home.models import (
    AlertSendStatus,
    AlertTerm,
    HomeAlert,
    HomeAlertSent,
    HomeChannel,
    HomeListing,
    ListingKind,
    ListingStatus,
    PostStatus,
)

logger = logging.getLogger(__name__)

CURRENCY_BY_COUNTRY = {"CA": "CAD", "DE": "EUR"}
# Budget bands offered in the bot, per currency (monthly rent ceilings). Any other number can be typed.
BUDGET_BANDS = {
    "CAD": [800, 1000, 1200, 1500, 1800, 2000, 2500, 3000],
    "EUR": [400, 500, 600, 750, 900, 1000, 1200, 1500],
}
SELF_CONTAINED = ["studio", "apartment", "basement", "house"]
# Bot choices: short code -> (stored property types, Persian label). Empty list: any type.
TYPE_CHOICES = {
    "r": (["room"], "اتاق"),
    "s": (["studio"], "استودیو"),
    "a": (["apartment", "basement", "house"], "آپارتمان یا سوئیت"),
    "c": (SELF_CONTAINED, "واحد مستقل (هر نوع)"),
    "x": ([], "فرقی نمی‌کنه"),
}
TERM_CHOICES = {
    "l": (AlertTerm.LEASE, "بلندمدت"),
    "s": (AlertTerm.SUBLET, "سابلت یا موقت"),
    "t": (AlertTerm.SHORT, "کوتاه‌مدت (چند شب)"),
    "x": (AlertTerm.ANY, "فرقی نمی‌کنه"),
}
GENDER_CHOICES = {"f": ("female", "خانم"), "m": ("male", "آقا"), "c": ("couple", "زوج"), "x": ("any", "مهم نیست")}
# The three setup questions, in order; then the optional ones behind «🎯 دقیق‌ترش کنید».
MAIN_FIELDS = ("type", "budget", "area")
ALL_FIELDS = ("type", "budget", "area", "gender", "rooms", "term", "registration")
PHYSICAL_TYPES = {"room", "studio", "apartment", "house", "basement"}
_ROOM_WORDS = re.compile(
    r"\b(?:shared|room in|roommate|room for|wg|wg zimmer|chambre|colocation|zimmer in|zimmer zur)\b|اتاق(?!\s*خواب)|هم[‌ ]?خانه|هم[‌ ]?خونه",
    re.IGNORECASE,
)
_HOME_WORDS = re.compile(
    r"\b(?:studio|bachelor|suite|apartment|apt|flat|condo|basement|\d\s*(?:-|\s)?bed(?:room)?s?|junior|wohnung|appartement|logement|\d\s*(?:1/2|½))\b"
    r"|آپارتمان|سوئیت|سوییت|استودیو|بیسمنت|واحد|خوابه|اتاق\s*خواب",
    re.IGNORECASE,
)
CITY_FA = {
    "toronto": "تورنتو",
    "montreal": "مونترال",
    "vancouver": "ونکوور",
    "berlin": "برلین",
    "hamburg": "هامبورگ",
    "cologne": "کلن",
}
MONTHLY_FACTOR = {"month": Decimal("1"), "week": Decimal("4.33"), "night": Decimal("30")}
WINDOW_DAYS = 3
SEARCH_DAYS = 30
CANDIDATE_LIMIT = 500
PER_MESSAGE_RESERVE_SECONDS = 2
APP_PREFIX = "app_"
MAX_ALERTS_PER_USER = 5
# More matches than this for one user in one run go out as one summary message.
SUMMARY_AT = 5
SUMMARY_LINES = 10
# Quiet hours, city time: from 23:00 until 08:00. None switches them off (tests do).
QUIET_HOURS: tuple[int, int] | None = (23, 8)
COUNTRY_TZ = {"CA": "America/Toronto", "DE": "Europe/Berlin"}
CITY_TZ = {"vancouver": "America/Vancouver", "calgary": "America/Edmonton"}
PAUSE_DAYS = 7
FEEDBACK_REASONS = {"p": ("price", "💶 قیمت"), "a": ("area", "📍 محله"), "t": ("type", "🏷 نوع"), "o": ("stale", "🕰 کهنه بود")}


def city_slug(name: str) -> str:
    """Lower-case letters and digits only, as in the start payload alert_home_<cc>_<city>."""
    return re.sub(r"[^0-9a-z]", "", (name or "").casefold())


def city_fa(name: str) -> str:
    return CITY_FA.get(city_slug(name), name)


# ---------------------------------------------------------------------------
# Listing facts used by both the bot and the Mini App filters.


def short_q() -> Q:
    return Q(rental_mode="short_term") | Q(price_period__in=["week", "night"])


def _temporary_q() -> Q:
    return (
        Q(rental_mode="sublet")
        | Q(available_to__isnull=False)
        | Q(flags__temporary=True)
        | Q(flags__untermiete=True)
        | Q(flags__sublet=True)
    )


def sublet_q() -> Q:
    return _temporary_q() & ~short_q()


def lease_q() -> Q:
    # Negating a JSON key lookup drops rows without the key, so the temporary ones are excluded by id.
    temporary = HomeListing.objects.filter(_temporary_q()).values("pk")
    return ~short_q() & ~Q(pk__in=temporary)


def lease_transfer_q() -> Q:
    return Q(rental_mode="lease_transfer")


def term_q(term: str) -> Q | None:
    """Mini App rental mode filter: lease, sublet, lease_transfer or short (stay)."""
    return {"lease": lease_q, "sublet": sublet_q, "lease_transfer": lease_transfer_q, "short": short_q}.get(term, lambda: None)()


def listing_term(listing: HomeListing) -> str:
    """short, sublet or lease. The same rules as `term_q`: a temporary or Untermiete post without dates is a sublet."""
    if listing.rental_mode == "short_term" or listing.price_period in {"week", "night"}:
        return "short"
    flags = listing.flags or {}
    if listing.rental_mode == "sublet" or listing.available_to or any(flags.get(key) is True for key in ("temporary", "untermiete", "sublet")):
        return "sublet"
    return "lease"


def listing_text(listing: HomeListing) -> str:
    post_text = listing.post.text if listing.post_id else ""
    return " ".join(part for part in (listing.summary_fa, listing.summary_en, post_text) if part)


def physical_type(listing: HomeListing) -> str:
    """room, studio, apartment, house or basement; for type other, read from the text.
    'home' when the text says a whole place without saying which, '' when it cannot tell."""
    if listing.property_type in PHYSICAL_TYPES:
        return listing.property_type
    text = listing_text(listing)
    if _ROOM_WORDS.search(text):
        return "room"
    if _HOME_WORDS.search(text):
        return "home"
    return ""


def type_fits(wanted: list[str], listing: HomeListing) -> bool:
    if not wanted:
        return True
    kind = physical_type(listing)
    if not kind:
        return True
    if kind == "home":
        return any(value in SELF_CONTAINED for value in wanted)
    return kind in wanted


def monthly_price(listing: HomeListing) -> Decimal | None:
    if listing.price_amount is None:
        return None
    return listing.price_amount * MONTHLY_FACTOR.get(listing.price_period, Decimal("1"))


def metro_channel(country: str, city: str, channels: list[HomeChannel] | None = None) -> HomeChannel | None:
    """The city channel row of a metro, matched by its name or an alias."""
    wanted = (city or "").strip().casefold()
    if not wanted:
        return None
    rows = channels if channels is not None else list(HomeChannel.objects.exclude(city=""))
    for channel in rows:
        if channel.country == country and channel.city and wanted in channel.names():
            return channel
    return None


def in_metro(listing: HomeListing, country: str, city: str, channel: HomeChannel | None) -> bool:
    from home.publish import route

    if listing.country != country:
        return False
    if channel is not None:
        return route(listing, [channel]) is channel
    wanted = city.strip().casefold()
    places = {part.strip().casefold() for value in (listing.city, listing.area) for part in [value or "", *(value or "").split(",")]}
    return wanted in places


def matches(alert: HomeAlert, listing: HomeListing, channel: HomeChannel | None = None) -> bool:
    """Does this offer belong to the alert? `channel` is the alert's metro row, if any."""
    if listing.kind != ListingKind.OFFER:
        return False
    if not in_metro(listing, alert.country, alert.city, channel):
        return False
    answered = set(alert.answered or [])
    kind = physical_type(listing)
    whole_home = kind != "room"
    # A field never answered is strict: only listings that state it.
    if "budget" not in answered and listing.price_amount is None:
        return False
    if "type" not in answered and not kind:
        return False
    if "gender" not in answered and listing.roommate_gender in {"female", "male", "couple"}:
        return False
    if "rooms" in needed_fields(alert) and "rooms" not in answered and whole_home and listing.rooms is None and listing.size_m2 is None:
        return False
    if "registration" in needed_fields(alert) and "registration" not in answered and listing.registration not in {"yes", "no"}:
        return False
    if alert.max_price:
        # Budget answered with a number: a listing without a price still goes out, marked «قیمت: نامشخص».
        price = monthly_price(listing)
        if price is not None and price > alert.max_price:
            return False
        if alert.currency and listing.price_currency and listing.price_currency != alert.currency:
            return False
    if not type_fits(alert.property_types or [], listing):
        return False
    term = listing_term(listing)
    allowed = {
        AlertTerm.LEASE: {"lease"},
        AlertTerm.SUBLET: {"sublet", "short"},
        AlertTerm.SHORT: {"short"},
    }.get(alert.term, {"lease", "sublet"})
    if term not in allowed:
        return False
    if not gender_fits(alert.gender, listing):
        return False
    if whole_home and alert.min_rooms and listing.rooms is not None and listing.rooms < alert.min_rooms:
        return False
    if whole_home and alert.min_size_m2 and listing.size_m2 is not None and listing.size_m2 < alert.min_size_m2:
        return False
    if alert.needs_registration and listing.registration == "no":
        return False
    if alert.area and not area_matches(alert.area, listing.area, listing.city, toronto=city_slug(alert.city) == "toronto"):
        return False
    return True


def needed_fields(alert: HomeAlert) -> list[str]:
    """Every question this alert can ask, in order. Rooms only for whole homes, Anmeldung only in Germany."""
    return list(MAIN_FIELDS) + refine_fields(alert)


def refine_fields(alert: HomeAlert) -> list[str]:
    """The optional questions behind «🎯 دقیق‌ترش کنید». Never answered, each one is strict (see `matches`)."""
    fields = ["gender"]
    if (alert.property_types or []) != ["room"]:
        fields.append("rooms")
    fields.append("term")
    if alert.country == "DE":
        fields.append("registration")
    return fields


def next_question(alert: HomeAlert) -> str:
    """The first of the three setup questions not answered yet, or ''."""
    answered = set(alert.answered or [])
    return next((field for field in MAIN_FIELDS if field not in answered), "")


def next_refine(alert: HomeAlert, after: str = "") -> str:
    """The next optional question after `after` that is not answered yet, or ''."""
    fields = refine_fields(alert)
    answered = set(alert.answered or [])
    start = fields.index(after) + 1 if after in fields else 0
    return next((field for field in fields[start:] if field not in answered), "")


def gender_fits(gender: str, listing: HomeListing) -> bool:
    """Women-only and men-only places reach only that alert; a couple skips single-person places."""
    if not gender or gender == "any":
        return True
    only = listing.roommate_gender
    if only in {"female", "male"} and only != gender:
        return False
    if only == "couple" and gender != "couple":
        return False
    if gender == "couple" and listing.occupants_wanted == 1:
        return False
    return True


def shown_offers():
    """Offers people may see: trusted, active, from accepted posts."""
    return HomeListing.objects.filter(
        kind=ListingKind.OFFER,
        status=ListingStatus.ACTIVE,
        needs_review=False,
        post__status=PostStatus.ACCEPTED,
    ).select_related("post")


def search(alert: HomeAlert, *, limit: int = 300, days: int = SEARCH_DAYS) -> list[HomeListing]:
    """Current listings of the last month (or `days`) that match, newest first."""
    channel = metro_channel(alert.country, alert.city)
    rows = (
        shown_offers()
        .filter(country=alert.country, last_seen__gte=timezone.now() - timedelta(days=days))
        .order_by("-last_seen", "-id")[:limit]
    )
    return [listing for listing in rows if matches(alert, listing, channel)]


def common_areas(country: str, city: str, *, limit: int = 8, days: int = SEARCH_DAYS) -> list[tuple[str, str, int]]:
    """The neighbourhoods with the most listings in a metro lately: (label, value, count), most first.

    `value` is what goes into `HomeAlert.area` (plain Latin, as `area_matches` reads it); `label` is the
    Persian name people use when there is one. In Toronto an intersection counts as its district.
    """
    from home.places import FA_TO_LATIN, fold, toronto_district

    channel = metro_channel(country, city)
    toronto = city_slug(city) == "toronto"
    metro = (city or "").strip().casefold()
    persian = {}
    for name_fa, latin in FA_TO_LATIN.items():
        persian.setdefault(latin, name_fa)
    counts: dict[str, list] = {}
    rows = shown_offers().filter(country=country, last_seen__gte=timezone.now() - timedelta(days=days)).order_by("-last_seen")[:CANDIDATE_LIMIT]
    for listing in rows:
        if not in_metro(listing, country, city, channel):
            continue
        raw = ""
        if toronto:
            raw = toronto_district(listing.area) or toronto_district(listing.city)
        if not raw:
            raw = (listing.area or "").split(",")[0].strip()
        if not raw and listing.city.strip().casefold() != metro:
            raw = listing.city.strip()
        key = fold(raw)
        if not key or key == fold(city) or len(raw) > 40:
            continue
        entry = counts.setdefault(key, [raw, 0])
        entry[1] += 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1][1], item[0]))[:limit]
    out = []
    for key, (raw, count) in ranked:
        value = key.title() if raw.casefold() == key else raw
        out.append((persian.get(key, value), value, count))
    return out


def summary(alert: HomeAlert, *, area: bool = True) -> str:
    """One Persian line: تورنتو · تا 1,500 CA$ · اتاق · سابلت."""
    from home.publish import money

    parts = [city_fa(alert.city)]
    if area and alert.area:
        parts[0] += f" ({alert.area})"
    parts.append(f"تا {money(alert.max_price, alert.currency)}" if alert.max_price else "هر قیمتی")
    for _code, (types, label) in TYPE_CHOICES.items():
        if types and sorted(types) == sorted(alert.property_types or []):
            parts.append(label)
            break
    else:
        if alert.property_types:
            from home.publish import TYPE_FA

            parts.append("، ".join(TYPE_FA.get(value, value) for value in alert.property_types))
    for _code, (term, label) in TERM_CHOICES.items():
        if term == alert.term and term != AlertTerm.ANY:
            parts.append(label)
    for _code, (gender, label) in GENDER_CHOICES.items():
        if gender == alert.gender and gender != "any":
            parts.append(label)
    if alert.min_rooms:
        parts.append(f"{fa_num(alert.min_rooms)}+ اتاق")
    if alert.min_size_m2:
        parts.append(f"{fa_num(alert.min_size_m2)}+ متر")
    if alert.needs_registration:
        parts.append("با ملد")
    return " · ".join(parts)


def fa_num(value) -> str:
    from home.publish import fa, num

    return fa(num(Decimal(value)))


# ---------------------------------------------------------------------------
# Saved requests: the user's own active wanted post in the Mini App.


def find_user(telegram_user_id: int):
    from users.models import User

    return User.objects.filter(telegram_user_id=telegram_user_id).first()


def sync_wanted_alerts() -> dict:
    """Give every active app "wanted" post an alert, and switch off alerts of closed ones."""
    from home.publish import route

    channels = list(HomeChannel.objects.exclude(city=""))
    created = 0
    fresh = HomeListing.objects.filter(
        kind=ListingKind.WANTED,
        status=ListingStatus.ACTIVE,
        post__source__username__startswith=APP_PREFIX,
        post__author_id__isnull=False,
        alert__isnull=True,
    ).select_related("post")
    for wanted in fresh:
        channel = route(wanted, channels)
        city = channel.city if channel else wanted.city
        if not city:
            continue
        if wanted.property_type in {"apartment", "basement", "house"}:
            types = TYPE_CHOICES["a"][0]
        else:
            types = [wanted.property_type] if wanted.property_type in PHYSICAL_TYPES else []
        term = {"sublet": AlertTerm.SUBLET, "short_term": AlertTerm.SHORT}.get(wanted.rental_mode, AlertTerm.ANY)
        # Only what the post states counts as answered; the rest stays strict.
        answered = ["area", "term"] + (["budget"] if wanted.price_max else []) + (["type"] if types else [])
        if wanted.roommate_gender in {"female", "male", "couple"}:
            answered.append("gender")
        area = wanted.area if wanted.area and wanted.area.casefold() != city.casefold() else ""
        HomeAlert.objects.create(
            telegram_user_id=wanted.post.author_id,
            chat_id=wanted.post.author_id,
            user=find_user(wanted.post.author_id),
            wanted_listing=wanted,
            country=wanted.country,
            city=city,
            max_price=wanted.price_max,
            currency=wanted.price_currency or CURRENCY_BY_COUNTRY.get(wanted.country, ""),
            property_types=types,
            term=term,
            area=area,
            gender=wanted.roommate_gender if wanted.roommate_gender in {"female", "male", "couple"} else "any",
            answered=answered,
            complete=True,
        )
        created += 1
    closed = (
        HomeAlert.objects.filter(active=True, wanted_listing__isnull=False)
        .exclude(wanted_listing__status=ListingStatus.ACTIVE)
        .update(active=False)
    )
    return {"created": created, "closed": closed}


# ---------------------------------------------------------------------------
# Time: quiet hours in the city's own clock, and «۸ دقیقه پیش».


def city_zone(country: str, city: str) -> ZoneInfo:
    name = CITY_TZ.get(city_slug(city)) or COUNTRY_TZ.get(country) or "UTC"
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - no tz database: UTC is better than no message
        return ZoneInfo("UTC")


def is_quiet(alert: HomeAlert, now: datetime | None = None) -> bool:
    """23:00 to 08:00 in the alert's city: matches wait for the first run after 08:00."""
    if not QUIET_HOURS:
        return False
    start, end = QUIET_HOURS
    local = (now or timezone.now()).astimezone(city_zone(alert.country, alert.city))
    return local.hour >= start or local.hour < end


def ago(when: datetime | None, now: datetime | None = None) -> str:
    """Persian relative time: همین الان, ۸ دقیقه پیش, ۳ ساعت پیش, ۲ روز پیش."""
    if when is None:
        return ""
    seconds = max(0, int(((now or timezone.now()) - when).total_seconds()))
    if seconds < 60:
        return "همین الان"
    if seconds < 3600:
        return f"{fa_int(seconds // 60)} دقیقه پیش"
    if seconds < 86400:
        return f"{fa_int(seconds // 3600)} ساعت پیش"
    return f"{fa_int(seconds // 86400)} روز پیش"


def fa_int(value) -> str:
    from home.publish import fa

    return fa(value)


def posted_at(listing: HomeListing) -> datetime:
    post = listing.post if listing.post_id else None
    return (post.posted_at if post is not None else None) or listing.created_at


def is_paused(alert: HomeAlert, now: datetime | None = None) -> bool:
    return bool(alert.paused_until and alert.paused_until > (now or timezone.now()))


def user_alerts(telegram_user_id: int):
    """The user's alerts in the order they are numbered («گوش به زنگ ۱»)."""
    return HomeAlert.objects.filter(telegram_user_id=telegram_user_id).order_by("created_at", "id")


def alert_number(alert: HomeAlert) -> int:
    ids = list(user_alerts(alert.telegram_user_id).values_list("pk", flat=True))
    return ids.index(alert.pk) + 1 if alert.pk in ids else len(ids) + 1


def active_count(telegram_user_id: int) -> int:
    """Alerts that count toward MAX_ALERTS_PER_USER: the user's own, switched on (a week's pause still counts)."""
    return HomeAlert.objects.filter(telegram_user_id=telegram_user_id, active=True, wanted_listing__isnull=True).count()


# ---------------------------------------------------------------------------
# Sending.


def fits_line(alert: HomeAlert) -> str:
    """«✓ می‌خوره: نوع، محله، بودجه»: the filters this alert set, which the listing passed."""
    parts = []
    if alert.property_types:
        parts.append("نوع")
    if alert.area:
        parts.append("محله")
    if alert.max_price:
        parts.append("بودجه")
    return f"✓ می‌خوره: {'، '.join(parts)}" if parts else ""


def alert_head(alert: HomeAlert, number: int | None = None) -> str:
    if alert.wanted_listing_id:
        return "درخواست شما"
    return f"گوش به زنگ {fa_int(number or alert_number(alert))}"


def alert_text(alert: HomeAlert, listing: HomeListing, *, number: int | None = None, now: datetime | None = None) -> str:
    """First line: which alert and how fresh. Then what the post does not say, the card, and what matched."""
    from home.publish import esc, format_listing_text

    head = f"🔔 <b>آگهی تازه برای {alert_head(alert, number)}</b>"
    fresh = ago(posted_at(listing), now)
    if fresh:
        head += f" · {fresh} منتشر شد"
    unknown = unknown_lines(listing)
    extra = ("\n" + "\n".join(unknown)) if unknown else ""
    fits = fits_line(alert)
    tail = f"\n\n<i>{esc(fits)}</i>" if fits else ""
    # Phone numbers are shown: people contact the lister freely (Mohammad, 2026-10-25).
    card = format_listing_text(listing, limit=3300, phone=True)
    return f"{head}\n<i>{esc(summary(alert))}</i>{extra}\n\n{card}{tail}"


def unknown_lines(listing: HomeListing) -> list[str]:
    """What the listing does not say, written as «نامشخص» so a match is never better than its post."""
    lines = []
    if listing.price_amount is None:
        lines.append("💶 قیمت: نامشخص (بپرسید)")
    if listing.size_m2 is None and not (listing.rooms is not None and physical_type(listing) != "room"):
        lines.append("📐 متراژ: نامشخص")
    if listing.country == "DE" and listing.registration not in {"yes", "no"}:
        lines.append("📝 ملد (Anmeldung): نامشخص")
    return lines


def contact_button(listing: HomeListing) -> dict:
    """«💬 پیام به آگهی‌دهنده»: straight to their Telegram, else a message with the phone and the original post."""
    label = "💬 پیام به آگهی‌دهنده"
    if listing.contact_telegram:
        return {"text": label, "url": f"https://t.me/{listing.contact_telegram.lstrip('@')}"}
    return {"text": label, "callback_data": f"al:ct:{listing.pk}"}


def alert_keyboard(alert: HomeAlert, listing: HomeListing, channel: HomeChannel | None = None, *, feedback: bool = True) -> dict:
    """The match buttons: contact, details, 👍/👎, a week's pause, off. Without `feedback` once the user answered."""
    rows = [[contact_button(listing), {"text": "ℹ️ جزئیات", "url": conf.mini_app_link(listing.pk)}]]
    if feedback:
        rows.append(
            [
                {"text": "👍 به دردم خورد", "callback_data": f"al:fb:{listing.pk}:u"},
                {"text": "👎 به کارم نمیاد", "callback_data": f"al:fb:{listing.pk}:d"},
            ]
        )
    rows.append(
        [
            {"text": "⏸ یک هفته مکث", "callback_data": f"al:pw:{alert.pk}"},
            {"text": "🔕 خاموش", "callback_data": f"al:off:{alert.pk}"},
            {"text": "🚩 گزارش", "callback_data": f"hx:r:{listing.pk}"},
        ]
    )
    if channel is not None and channel.username:
        rows.append([{"text": f"کانال خانه‌ی {city_fa(channel.city)}", "url": f"https://t.me/{channel.username}"}])
    return {"inline_keyboard": rows}


def reason_keyboard(listing_id: int) -> dict:
    """👎 asks one tap: why not? The answer is stored on the sent row and read when tuning the matcher."""
    buttons = [{"text": label, "callback_data": f"al:fr:{listing_id}:{code}"} for code, (_value, label) in FEEDBACK_REASONS.items()]
    return {"inline_keyboard": [buttons[0:2], buttons[2:4]]}


def send_alert(alert: HomeAlert, listing: HomeListing, channel: HomeChannel | None, *, number: int | None = None) -> int:
    result = bot.call(
        "sendMessage",
        {
            "chat_id": alert.chat_id,
            "text": alert_text(alert, listing, number=number),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": alert_keyboard(alert, listing, channel),
        },
    )
    return int(result["message_id"])


def summary_line(listing: HomeListing, number: int, now: datetime | None = None) -> str:
    from home.publish import TYPE_FA, esc, money

    price = money(listing.price_amount, listing.price_currency) if listing.price_amount else "قیمت: بپرسید"
    place = listing.area or listing.city
    kind = TYPE_FA.get(listing.property_type, "ملک")
    link = f'<a href="{esc(conf.mini_app_link(listing.pk))}">جزئیات</a>'
    return f"{fa_int(number)}. 💶 {esc(price)} · {esc(kind)} · {esc(place)} · {ago(posted_at(listing), now)} · {link}"


def summary_text(items: list[tuple[HomeAlert, HomeListing]], numbers: dict[int, int], now: datetime | None = None) -> str:
    """More than SUMMARY_AT matches at once: one message with one line each."""
    alerts: list[HomeAlert] = []
    for alert, _listing in items:
        if all(alert.pk != seen.pk for seen in alerts):
            alerts.append(alert)
    names = "، ".join(alert_head(alert, numbers.get(alert.pk)) for alert in alerts)
    lines = [summary_line(listing, index, now) for index, (_alert, listing) in enumerate(items[:SUMMARY_LINES], start=1)]
    more = len(items) - SUMMARY_LINES
    tail = f"\n… و {fa_int(more)} آگهی دیگر در «🔎 آگهی‌های فعلی»." if more > 0 else ""
    return f"🔔 <b>{fa_int(len(items))} آگهی تازه برای {names}</b>\n\n" + "\n".join(lines) + tail


def summary_keyboard(alert: HomeAlert) -> dict:
    return {
        "inline_keyboard": [
            [{"text": "🔎 آگهی‌های فعلی", "callback_data": f"sr:a:{alert.pk}:0"}],
            [
                {"text": "⏸ یک هفته مکث", "callback_data": f"al:pw:{alert.pk}"},
                {"text": "🔕 خاموش", "callback_data": f"al:off:{alert.pk}"},
            ],
        ]
    }


def send_summary(items: list[tuple[HomeAlert, HomeListing]], numbers: dict[int, int]) -> int:
    first = items[0][0]
    result = bot.call(
        "sendMessage",
        {
            "chat_id": first.chat_id,
            "text": summary_text(items, numbers),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": summary_keyboard(first),
        },
    )
    return int(result["message_id"])


def candidates():
    since = timezone.now() - timedelta(days=WINDOW_DAYS)
    return shown_offers().filter(created_at__gte=since, last_seen__gte=since).order_by("created_at", "id")


def notify_pending(*, limit: int | None = None, stop_at: float | None = None) -> dict:
    """Message the users whose alerts match listings accepted in the last few days. Never raises on Telegram trouble.

    Matches are gathered per user first: up to SUMMARY_AT go out one by one, more go out as one summary.
    The daily cap counts messages, so a summary is one. Paused alerts and quiet hours leave a match
    unrecorded, so a later run sends it.
    """
    blocker = conf.alerts_blocker()
    if blocker:
        if blocker == conf.BOT_TOKEN_MISSING:
            logger.error("housing alerts skipped: %s", blocker)
        return {"ok": True, "skipped": blocker, "sent": 0}
    synced = sync_wanted_alerts()
    now = timezone.now()
    alerts = list(HomeAlert.objects.filter(active=True).order_by("created_at", "id"))
    out = {
        "ok": True,
        "wanted_synced": synced,
        "alerts": len(alerts),
        "sent": 0,
        "summaries": 0,
        "blocked": 0,
        "failed": 0,
        "over_daily_cap": 0,
        "quiet": 0,
        "paused": 0,
        "retry_after": 0,
    }
    if not alerts:
        return out
    countries = {alert.country for alert in alerts}
    listings = list(candidates().filter(country__in=countries)[:CANDIDATE_LIMIT])
    if not listings:
        return out
    done = set(HomeAlertSent.objects.filter(listing__in=listings).values_list("telegram_user_id", "listing_id"))
    messages_today: dict[int, set] = {}
    for user_id, message_id, pk in HomeAlertSent.objects.filter(
        sent_at__gte=now - timedelta(days=1), status=AlertSendStatus.SENT
    ).values_list("telegram_user_id", "message_id", "pk"):
        messages_today.setdefault(user_id, set()).add(message_id or f"row{pk}")
    sent_today = {user_id: len(ids) for user_id, ids in messages_today.items()}
    cap = conf.alerts_daily_cap()
    all_channels = list(HomeChannel.objects.exclude(city=""))
    channel_of = {alert.pk: metro_channel(alert.country, alert.city, all_channels) for alert in alerts}
    numbers: dict[int, int] = {}
    counter: dict[int, int] = {}
    for pk, user_id in HomeAlert.objects.filter(telegram_user_id__in={a.telegram_user_id for a in alerts}).order_by(
        "created_at", "id"
    ).values_list("pk", "telegram_user_id"):
        counter[user_id] = counter.get(user_id, 0) + 1
        numbers[pk] = counter[user_id]
    quiet = {alert.pk: is_quiet(alert, now) for alert in alerts}
    pending: dict[int, list[tuple[HomeAlert, HomeListing]]] = {}
    picked: set[tuple[int, int]] = set()
    for listing in listings:
        for alert in alerts:
            user_id = alert.telegram_user_id
            key = (user_id, listing.pk)
            if key in done or key in picked:
                continue
            if listing.created_at < alert.created_at or listing.post.author_id == user_id:
                continue
            if not matches(alert, listing, channel_of[alert.pk]):
                continue
            if is_paused(alert, now):
                out["paused"] += 1
                continue
            if quiet[alert.pk]:
                out["quiet"] += 1
                continue
            picked.add(key)
            pending.setdefault(user_id, []).append((alert, listing))
    for user_id, items in pending.items():
        batches = [items] if len(items) > SUMMARY_AT else [[item] for item in items]
        for batch in batches:
            if sent_today.get(user_id, 0) >= cap:
                out["over_daily_cap"] += len(batch)
                continue
            if limit is not None and out["sent"] + out["failed"] >= limit:
                return out
            if stop_at is not None and time.monotonic() + PER_MESSAGE_RESERVE_SECONDS >= stop_at:
                return out
            alert, listing = batch[0]
            try:
                if len(batch) == 1:
                    message_id = send_alert(alert, listing, channel_of[alert.pk], number=numbers.get(alert.pk))
                else:
                    message_id = send_summary(batch, numbers)
            except bot.BotError as exc:
                if exc.retry_after:
                    out["retry_after"] = exc.retry_after
                    return out
                blocked = exc.code == 403
                if blocked:
                    HomeAlert.objects.filter(telegram_user_id=user_id).update(active=False)
                status = AlertSendStatus.BLOCKED if blocked else AlertSendStatus.FAILED
                for failed_alert, failed_listing in batch:
                    HomeAlertSent.objects.create(
                        telegram_user_id=user_id, listing=failed_listing, alert=failed_alert, status=status, error=exc.description[:200]
                    )
                out["blocked" if blocked else "failed"] += 1
                logger.info("Home alert to %s failed: %s", user_id, exc.description)
                if blocked:
                    break
                continue
            for sent_alert, sent_listing in batch:
                HomeAlertSent.objects.create(telegram_user_id=user_id, listing=sent_listing, alert=sent_alert, message_id=message_id)
            HomeAlert.objects.filter(pk__in={sent_alert.pk for sent_alert, _ in batch}).update(last_notified_at=timezone.now())
            sent_today[user_id] = sent_today.get(user_id, 0) + 1
            out["sent"] += len(batch)
            if len(batch) > 1:
                out["summaries"] += 1
    return out
