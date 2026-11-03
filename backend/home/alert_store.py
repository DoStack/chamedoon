"""F15 and F16 for the new Mini App: saving, editing and showing a person's alerts, on the same HomeAlert rows the bot uses."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from home import deeplink
from home import search as home_search
from home.alerts import ALL_FIELDS, CURRENCY_BY_COUNTRY, MAX_ALERTS_PER_USER, PAUSE_DAYS, SEARCH_DAYS, active_count, is_paused, matches, metro_channel, shown_offers
from home.models import AlertSendStatus, AlertTerm, HomeAlert, HomeAlertSent, HomeArea, HomeChatState, HomeListing

FILTER_KEYS = ("types", "min", "max", "areas", "term", "gender", "move", "by", *home_search.TOGGLES)
NEW_LIMIT = 300


class AlertCapReached(Exception):
    pass


class AlertDuplicate(Exception):
    def __init__(self, duplicate_id: int) -> None:
        super().__init__(duplicate_id)
        self.duplicate_id = duplicate_id


class AlertNotEditable(Exception):
    pass


def lock_person(user) -> None:
    """One save at a time for a person, so a double tap cannot pass the cap or make two rows."""
    type(user).objects.select_for_update().filter(pk=user.pk).first()


def _search_of(filters: dict) -> dict:
    """What HomeAlert.search keeps. `unpriced` is kept even when false: a budget then leaves out listings with no price, as the feed does."""
    search = home_search.to_search(filters)
    if filters.get("area_ids"):
        search["area_ids"] = sorted(filters["area_ids"])
    if search.get("min") or search.get("max"):
        search["unpriced"] = bool(filters.get("unpriced"))
    return search


def _with_area_names(filters: dict, country: str, city: str) -> dict:
    """Matching reads area names, so ids become their names here."""
    ids = filters.get("area_ids") or []
    if not ids:
        return filters
    names = list(HomeArea.objects.filter(pk__in=ids, country=country, city=city).values_list("canonical", flat=True))
    return {**filters, "areas": sorted({*(filters.get("areas") or []), *names})}


def _columns(filters: dict, country: str) -> dict:
    """The columns the bot's matching reads, from the same filters as save_search."""
    term = {"lease": AlertTerm.LEASE, "sublet": AlertTerm.SUBLET, "short": AlertTerm.SHORT}.get(filters.get("term") or "", AlertTerm.ANY)
    return {
        "max_price": Decimal(filters["max"]) if filters.get("max") else None,
        "currency": CURRENCY_BY_COUNTRY.get(country, ""),
        "property_types": list(filters.get("types") or []),
        "term": term,
        "area": ", ".join(filters.get("areas") or [])[:96],
        "gender": filters.get("gender") or "any",
        "needs_registration": bool(filters.get("reg")),
    }


def save_alert(user, country: str, city: str, filters: dict, delivery: str) -> tuple[HomeAlert, str]:
    """Returns the alert and created, duplicate or reactivated. A retry finds the same row."""
    telegram_id = user.telegram_user_id
    filters = _with_area_names(filters, country, city)
    search = _search_of(filters)
    with transaction.atomic():
        lock_person(user)
        same = next(
            (alert for alert in HomeAlert.objects.filter(telegram_user_id=telegram_id, country=country, city=city, wanted_listing__isnull=True) if alert.search == search),
            None,
        )
        if same is not None:
            if same.active and not is_paused(same):
                return same, "duplicate"
            if not same.active and active_count(telegram_id) >= MAX_ALERTS_PER_USER:
                raise AlertCapReached
            same.active = True
            same.paused_until = None
            same.save(update_fields=["active", "paused_until"])
            return same, "reactivated"
        if active_count(telegram_id) >= MAX_ALERTS_PER_USER:
            raise AlertCapReached
        alert = HomeAlert.objects.create(
            telegram_user_id=telegram_id,
            chat_id=telegram_id,
            user=user,
            country=country,
            city=city,
            answered=list(ALL_FIELDS),
            complete=True,
            search=search,
            delivery=delivery,
            viewed_at=timezone.now(),
            **_columns(filters, country),
        )
    return alert, "created"


def update_alert(user, alert: HomeAlert, changes: dict) -> HomeAlert:
    """F16: only the fields sent. Sending what is already true changes nothing."""
    if alert.wanted_listing_id and ("filters" in changes or "delivery" in changes):
        raise AlertNotEditable
    with transaction.atomic():
        lock_person(user)
        fields: list[str] = []
        if "filters" in changes:
            filters = _with_area_names(changes["filters"], alert.country, alert.city)
            search = _search_of(filters)
            other = HomeAlert.objects.filter(telegram_user_id=alert.telegram_user_id, country=alert.country, city=alert.city, wanted_listing__isnull=True).exclude(pk=alert.pk)
            match = next((row for row in other if row.search == search), None)
            if match is not None:
                raise AlertDuplicate(match.pk)
            columns = _columns(filters, alert.country)
            for name, value in columns.items():
                setattr(alert, name, value)
            alert.search = search
            alert.answered = list(ALL_FIELDS)
            alert.complete = True
            fields += [*columns, "search", "answered", "complete"]
        if "delivery" in changes:
            alert.delivery = changes["delivery"]
            fields.append("delivery")
        if "alert_state" in changes:
            _set_state(alert, changes["alert_state"])
            fields += ["active", "paused_until"]
        if fields:
            alert.save(update_fields=fields)
    return alert


def _set_state(alert: HomeAlert, state: str) -> None:
    if state == "off":
        alert.active, alert.paused_until = False, None
        return
    if not alert.active and not alert.wanted_listing_id and active_count(alert.telegram_user_id) >= MAX_ALERTS_PER_USER:
        raise AlertCapReached
    alert.active = True
    if state == "active":
        alert.paused_until = None
    elif not is_paused(alert):
        alert.paused_until = timezone.now() + timedelta(days=PAUSE_DAYS)


def alert_state(alert: HomeAlert) -> str:
    if not alert.active:
        return "off"
    return "paused" if is_paused(alert) else "active"


def origin(alert: HomeAlert) -> str:
    if alert.wanted_listing_id:
        return "wanted"
    return "app" if alert.search else "bot"


def alert_row(alert: HomeAlert, filters: dict, new_count: int) -> dict:
    """`filters` is what miniapp's alert_filters reads for this row, so bot alerts show their type, budget and area too."""
    channel = metro_channel(alert.country, alert.city)
    shown = {key: filters[key] for key in FILTER_KEYS if key in filters}
    search = alert.search or {}
    return {
        "id": alert.pk,
        "country": alert.country,
        "city": alert.city,
        "city_slug": deeplink.slug(alert.city),
        "city_active": bool(channel and channel.enabled),
        "filters": {**shown, "unpriced": bool(search.get("unpriced")), "area_ids": search.get("area_ids") or []},
        "delivery": alert.delivery,
        "alert_state": alert_state(alert),
        "paused_until": alert.paused_until if is_paused(alert) else None,
        "origin": origin(alert),
        "wanted_listing_id": alert.wanted_listing_id,
        "new_count": new_count,
        "created_at": alert.created_at,
    }


def new_listings(alert: HomeAlert) -> list[HomeListing]:
    """F16#6: what matched since it was last viewed, newest first. The same window as «n جدید»."""
    if not alert.active:
        return []
    since = max(alert.viewed_at or alert.created_at, timezone.now() - timedelta(days=SEARCH_DAYS))
    channel = metro_channel(alert.country, alert.city)
    rows = shown_offers().filter(country=alert.country, first_seen__gt=since).order_by("-first_seen")[:NEW_LIMIT]
    return [listing for listing in rows if matches(alert, listing, channel)]


def bot_open(telegram_id: int) -> bool:
    """Has this person ever opened the bot? The bot keeps a state row from the first chat, and a message that arrived proves it too."""
    return (
        HomeChatState.objects.filter(telegram_user_id=telegram_id).exists()
        or HomeAlertSent.objects.filter(telegram_user_id=telegram_id, status=AlertSendStatus.SENT).exists()
    )


def bot_blocked(telegram_id: int) -> bool:
    """The last send got a 403, which turned the alerts off. Turning one on clears this, and the next send answers again."""
    last = HomeAlertSent.objects.filter(telegram_user_id=telegram_id).order_by("-sent_at", "-id").values_list("status", flat=True).first()
    return last == AlertSendStatus.BLOCKED and not HomeAlert.objects.filter(telegram_user_id=telegram_id, active=True).exists()
