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
# Budget bands offered in the bot, per currency (monthly rent ceilings).
BUDGET_BANDS = {"CAD": [1000, 1500, 2000, 2500, 3000], "EUR": [500, 750, 1000, 1500, 2000]}
# Bot choices: short code -> (stored property types, Persian label). Empty list: any type.
TYPE_CHOICES = {
    "r": (["room"], "اتاق"),
    "s": (["studio"], "استودیو"),
    "a": (["apartment", "basement"], "آپارتمان ۱½ تا ۲½"),
    "x": ([], "فرقی نمی‌کنه"),
}
TERM_CHOICES = {"l": (AlertTerm.LEASE, "اجاره‌ی بلندمدت"), "s": (AlertTerm.SUBLET, "سابلت یا کوتاه‌مدت"), "x": (AlertTerm.ANY, "فرقی نمی‌کنه")}
PHYSICAL_TYPES = {"room", "studio", "apartment", "house", "basement"}
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


def sublet_q() -> Q:
    return (Q(property_type="sublet") | Q(available_to__isnull=False)) & ~short_q()


def lease_q() -> Q:
    return ~short_q() & ~Q(property_type="sublet") & Q(available_to__isnull=True)


def term_q(term: str) -> Q | None:
    """Mini App term filter: lease, sublet or short (stay)."""
    return {"lease": lease_q, "sublet": sublet_q, "short": short_q}.get(term, lambda: None)()


def listing_term(listing: HomeListing) -> str:
    """short, sublet or lease. The same rules as `term_q`."""
    if listing.property_type == "short_stay" or listing.price_period in {"week", "night"}:
        return "short"
    if listing.property_type == "sublet" or listing.available_to:
        return "sublet"
    return "lease"


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
    if alert.max_price:
        price = monthly_price(listing)
        if price is None or price > alert.max_price:
            return False
        if alert.currency and listing.price_currency and listing.price_currency != alert.currency:
            return False
    if alert.property_types and listing.property_type not in alert.property_types:
        return False
    if alert.term == AlertTerm.LEASE and listing_term(listing) != "lease":
        return False
    if alert.term == AlertTerm.SUBLET and listing_term(listing) == "lease":
        return False
    if alert.area:
        place = f"{listing.area} {listing.city}".casefold()
        if alert.area.strip().casefold() not in place:
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
    return " · ".join(parts)


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
        types = [wanted.property_type] if wanted.property_type in PHYSICAL_TYPES else []
        term = AlertTerm.SUBLET if wanted.property_type in {"sublet", "short_stay"} else AlertTerm.ANY
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
    return f"{head}\n<i>{esc(summary(alert))}</i>\n\n{format_listing_text(listing, limit=3500)}"


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
