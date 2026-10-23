"""«گوش به زنگ»: match new listings to saved alerts and message each user once per listing.

An alert is made in the housing bot (home/botchat.py) or copied from the user's own
active "wanted" post in the Mini App, so a saved request is notified the same way.
A listing matches when it is an offer in the alert's metro (the city channel's aliases
bring the suburbs in, through the same `route()` the channel posting uses), under the
budget, of the wanted type and term, and in the neighbourhood if one was given.

Sending is a job step (`alerts`) with its own switch in Home settings, off by default.
A user hears about a listing once, at most `alerts_daily_cap` messages a day; a user who
blocked the bot has their alerts switched off.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import timedelta
from decimal import Decimal

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
ALL_FIELDS = ("gender", "budget", "type", "rooms", "term", "registration")
PHYSICAL_TYPES = {"room", "studio", "apartment", "house", "basement"}
# Listing types that say how long, not what kind of place: the kind is read from the text.
TERM_TYPES = {"sublet", "lease_transfer", "short_stay", "other"}
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


def city_slug(name: str) -> str:
    """Lower-case letters and digits only, as in the start payload alert_home_<cc>_<city>."""
    return re.sub(r"[^0-9a-z]", "", (name or "").casefold())


def city_fa(name: str) -> str:
    return CITY_FA.get(city_slug(name), name)


# ---------------------------------------------------------------------------
# Listing facts used by both the bot and the Mini App filters.


def short_q() -> Q:
    return Q(property_type="short_stay") | Q(price_period__in=["week", "night"])


def _temporary_q() -> Q:
    return (
        Q(property_type="sublet")
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


def term_q(term: str) -> Q | None:
    """Mini App term filter: lease, sublet or short (stay)."""
    return {"lease": lease_q, "sublet": sublet_q, "short": short_q}.get(term, lambda: None)()


def listing_term(listing: HomeListing) -> str:
    """short, sublet or lease. The same rules as `term_q`: a temporary or Untermiete post without dates is a sublet."""
    if listing.property_type == "short_stay" or listing.price_period in {"week", "night"}:
        return "short"
    flags = listing.flags or {}
    if listing.property_type == "sublet" or listing.available_to or any(flags.get(key) is True for key in ("temporary", "untermiete", "sublet")):
        return "sublet"
    return "lease"


def listing_text(listing: HomeListing) -> str:
    post_text = listing.post.text if listing.post_id else ""
    return " ".join(part for part in (listing.summary_fa, listing.summary_en, post_text) if part)


def physical_type(listing: HomeListing) -> str:
    """room, studio, apartment, house or basement; for a sublet or lease transfer, read from the text.
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
    """The setup questions this alert asks, in order. Rooms only for whole homes, Anmeldung only in Germany."""
    fields = ["gender", "budget", "type"]
    if (alert.property_types or []) != ["room"]:
        fields.append("rooms")
    fields.append("term")
    if alert.country == "DE":
        fields.append("registration")
    return fields


def next_question(alert: HomeAlert) -> str:
    """The first question not answered yet, or ''."""
    answered = set(alert.answered or [])
    return next((field for field in needed_fields(alert) if field not in answered), "")


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


def search(alert: HomeAlert, *, limit: int = 300) -> list[HomeListing]:
    """Current listings of the last month that match, newest first."""
    channel = metro_channel(alert.country, alert.city)
    rows = (
        shown_offers()
        .filter(country=alert.country, last_seen__gte=timezone.now() - timedelta(days=SEARCH_DAYS))
        .order_by("-last_seen", "-id")[:limit]
    )
    return [listing for listing in rows if matches(alert, listing, channel)]


def summary(alert: HomeAlert) -> str:
    """One Persian line: تورنتو · تا 1,500 CA$ · اتاق · سابلت."""
    from home.publish import money

    parts = [city_fa(alert.city)]
    if alert.area:
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
        term = {"sublet": AlertTerm.SUBLET, "short_stay": AlertTerm.SHORT}.get(wanted.property_type, AlertTerm.ANY)
        # Only what the post states counts as answered; the rest stays strict.
        answered = ["term"] + (["budget"] if wanted.price_max else []) + (["type"] if types else [])
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
# Sending.


def alert_text(alert: HomeAlert, listing: HomeListing) -> str:
    from home.publish import esc, format_listing_text

    head = "🔔 <b>آگهی تازه برای درخواست شما</b>" if alert.wanted_listing_id else "🔔 <b>آگهی تازه برای گوش به زنگ شما</b>"
    unknown = unknown_lines(listing)
    extra = ("\n" + "\n".join(unknown)) if unknown else ""
    return f"{head}\n<i>{esc(summary(alert))}</i>{extra}\n\n{format_listing_text(listing, limit=3500)}"


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


def alert_keyboard(alert: HomeAlert, listing: HomeListing, channel: HomeChannel | None) -> dict:
    rows = [
        [
            {"text": "👀 دیدم", "callback_data": f"al:seen:{listing.pk}"},
            {"text": "🔕 دیگه نمی‌خوام", "callback_data": f"al:off:{alert.pk}"},
        ],
        [{"text": "جزئیات در چمدون خانه", "url": conf.mini_app_link(listing.pk)}],
    ]
    if channel is not None and channel.username:
        rows.append([{"text": f"کانال خانه‌ی {city_fa(channel.city)}", "url": f"https://t.me/{channel.username}"}])
    return {"inline_keyboard": rows}


def send_alert(alert: HomeAlert, listing: HomeListing, channel: HomeChannel | None) -> int:
    result = bot.call(
        "sendMessage",
        {
            "chat_id": alert.chat_id,
            "text": alert_text(alert, listing),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": alert_keyboard(alert, listing, channel),
        },
    )
    return int(result["message_id"])


def candidates():
    since = timezone.now() - timedelta(days=WINDOW_DAYS)
    return shown_offers().filter(created_at__gte=since, last_seen__gte=since).order_by("created_at", "id")


def notify_pending(*, limit: int | None = None, stop_at: float | None = None) -> dict:
    """Message the users whose alerts match listings accepted in the last few days. Never raises on Telegram trouble."""
    blocker = conf.alerts_blocker()
    if blocker:
        if blocker == conf.BOT_TOKEN_MISSING:
            logger.error("housing alerts skipped: %s", blocker)
        return {"ok": True, "skipped": blocker, "sent": 0}
    synced = sync_wanted_alerts()
    alerts = list(HomeAlert.objects.filter(active=True).order_by("created_at", "id"))
    out = {"ok": True, "wanted_synced": synced, "alerts": len(alerts), "sent": 0, "blocked": 0, "failed": 0, "over_daily_cap": 0, "retry_after": 0}
    if not alerts:
        return out
    countries = {alert.country for alert in alerts}
    listings = list(candidates().filter(country__in=countries)[:CANDIDATE_LIMIT])
    if not listings:
        return out
    done = set(
        HomeAlertSent.objects.filter(listing__in=listings).values_list("telegram_user_id", "listing_id")
    )
    day_ago = timezone.now() - timedelta(days=1)
    sent_today: dict[int, int] = {}
    for user_id in HomeAlertSent.objects.filter(sent_at__gte=day_ago, status=AlertSendStatus.SENT).values_list(
        "telegram_user_id", flat=True
    ):
        sent_today[user_id] = sent_today.get(user_id, 0) + 1
    cap = conf.alerts_daily_cap()
    all_channels = list(HomeChannel.objects.exclude(city=""))
    channel_of = {alert.pk: metro_channel(alert.country, alert.city, all_channels) for alert in alerts}
    blocked: set[int] = set()
    for listing in listings:
        for alert in alerts:
            user_id = alert.telegram_user_id
            if user_id in blocked or not alert.active or (user_id, listing.pk) in done:
                continue
            if listing.created_at < alert.created_at or listing.post.author_id == user_id:
                continue
            channel = channel_of[alert.pk]
            if not matches(alert, listing, channel):
                continue
            if sent_today.get(user_id, 0) >= cap:
                out["over_daily_cap"] += 1
                continue
            if limit is not None and out["sent"] + out["failed"] >= limit:
                return out
            if stop_at is not None and time.monotonic() + PER_MESSAGE_RESERVE_SECONDS >= stop_at:
                return out
            done.add((user_id, listing.pk))
            try:
                message_id = send_alert(alert, listing, channel)
            except bot.BotError as exc:
                if exc.retry_after:
                    done.discard((user_id, listing.pk))
                    out["retry_after"] = exc.retry_after
                    return out
                if exc.code == 403:
                    blocked.add(user_id)
                    HomeAlert.objects.filter(telegram_user_id=user_id).update(active=False)
                    for other in alerts:
                        if other.telegram_user_id == user_id:
                            other.active = False
                    status, out_key = AlertSendStatus.BLOCKED, "blocked"
                else:
                    status, out_key = AlertSendStatus.FAILED, "failed"
                HomeAlertSent.objects.create(
                    telegram_user_id=user_id, listing=listing, alert=alert, status=status, error=exc.description[:200]
                )
                out[out_key] += 1
                logger.info("Home alert to %s failed: %s", user_id, exc.description)
                continue
            HomeAlertSent.objects.create(telegram_user_id=user_id, listing=listing, alert=alert, message_id=message_id)
            alert.last_notified_at = timezone.now()
            alert.save(update_fields=["last_notified_at"])
            sent_today[user_id] = sent_today.get(user_id, 0) + 1
            out["sent"] += 1
    return out
