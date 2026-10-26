"""«🔔 گوش به زنگ مسیر»: a saved route, date window and weight, for either side.

When a new request is saved (in the bot, the Mini App or by the channel import) every active
alert of the other side that fits gets one message. The messages are behind the alerts switch
(off by default); with it off nothing is sent and nothing is recorded, so switching it on later
does not replay old requests. A person keeps at most MAX_ALERTS_PER_USER alerts.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from courier import conf, search, texts
from courier.models import CourierAlertSent, CourierRouteAlert
from courier.telegram import execute, send
from item_requests.models import ItemRequest, RequestStatus, RequestType
from matching.rules import is_matchable, is_person_request

logger = logging.getLogger(__name__)

OPEN_WINDOW_DAYS = 90


class AlertCapError(Exception):
    pass


def user_alerts(telegram_user_id: int):
    return CourierRouteAlert.objects.filter(telegram_user_id=telegram_user_id).order_by("created_at")


def create_alert(
    *,
    telegram_user_id: int,
    chat_id: int,
    user,
    role: str,
    origin: tuple[str, str],
    destination: tuple[str, str],
    date_from: date | None = None,
    date_to: date | None = None,
    weight_kg: Decimal | None = None,
) -> tuple[CourierRouteAlert, bool]:
    """(alert, created). The same route and side again turns the old one back on. Raises AlertCapError."""
    role = RequestType.SUPPLY if role == RequestType.SUPPLY else RequestType.DEMAND
    same = user_alerts(telegram_user_id).filter(
        role=role,
        origin_country=origin[0],
        origin_city=origin[1],
        destination_country=destination[0],
        destination_city=destination[1],
    ).first()
    if same is not None:
        same.active = True
        same.date_from, same.date_to, same.weight_kg = date_from, date_to, weight_kg
        same.save(update_fields=["active", "date_from", "date_to", "weight_kg"])
        return same, False
    if user_alerts(telegram_user_id).count() >= conf.MAX_ALERTS_PER_USER:
        raise AlertCapError()
    alert = CourierRouteAlert.objects.create(
        telegram_user_id=telegram_user_id,
        chat_id=chat_id,
        user=user,
        role=role,
        origin_country=origin[0],
        origin_city=origin[1],
        destination_country=destination[0],
        destination_city=destination[1],
        date_from=date_from,
        date_to=date_to,
        weight_kg=weight_kg,
    )
    return alert, True


def alert_from_request(item: ItemRequest, *, telegram_user_id: int, chat_id: int) -> tuple[CourierRouteAlert, bool]:
    today = timezone.localdate()
    if item.type == RequestType.DEMAND:
        window = (today, item.desired_date or item.date_to)
    else:
        window = (today, item.flight_date or item.date_to)
    return create_alert(
        telegram_user_id=telegram_user_id,
        chat_id=chat_id,
        user=item.user,
        role=item.type,
        origin=(item.origin_country, item.origin_city),
        destination=(item.destination_country, item.destination_city),
        date_from=window[0],
        date_to=window[1],
        weight_kg=item.kg_value(),
    )


def matches(alert: CourierRouteAlert, item: ItemRequest, *, today: date | None = None) -> bool:
    today = today or timezone.localdate()
    if not alert.active or item.type != search.opposite(alert.role):
        return False
    if int(item.user.telegram_user_id) == int(alert.telegram_user_id):
        return False
    if (item.origin_country, item.origin_city) != (alert.origin_country, alert.origin_city):
        return False
    if (alert.destination_country, alert.destination_city) not in item.destination_stop_pairs():
        return False
    day = search.request_date(item)
    if day is not None and day < today:
        return False
    if alert.date_to and day is not None and day > alert.date_to:
        return False
    if alert.date_from and day is not None and day < alert.date_from:
        return False
    if alert.weight_kg is not None:
        if item.type == RequestType.SUPPLY and item.capacity_kg is not None and item.capacity_kg < alert.weight_kg:
            return False
        if item.type == RequestType.DEMAND and item.weight_kg is not None and item.weight_kg > alert.weight_kg:
            return False
    return True


def summary(alert: CourierRouteAlert) -> str:
    route = texts.route_text(alert.origin_country, alert.origin_city, [(alert.destination_country, alert.destination_city)])
    side = "📦" if alert.role == RequestType.DEMAND else "🧳"
    parts = [f"{side} {route}"]
    if alert.date_to:
        parts.append(f"تا {texts.day_month(alert.date_to)}")
    if alert.weight_kg is not None:
        parts.append(texts.kg_text(alert.weight_kg))
    return " · ".join(parts)


def alert_message(alert: CourierRouteAlert, item: ItemRequest) -> tuple[str, list]:
    who = "مسافر جدید" if item.type == RequestType.SUPPLY else "بسته‌ی جدید"
    lines = [
        f"🔔 <b>{who}</b> برای مسیر شما: <b>{texts.esc(texts.request_route(item))}</b>",
        "",
        *search.card_lines(item),
    ]
    keyboard = [
        [{"text": "💬 معرفی", "callback_data": f"cr:i:{item.pk}"}],
        [{"text": "🔕 خاموش کردن این گوش به زنگ", "callback_data": f"cr:al:p:{alert.pk}"}],
    ]
    return "\n".join(lines), keyboard


def _sent_last_day(telegram_user_id: int) -> int:
    since = timezone.now() - timedelta(days=1)
    return CourierAlertSent.objects.filter(alert__telegram_user_id=telegram_user_id, sent_at__gte=since).count()


def due_alerts(item: ItemRequest) -> list[CourierRouteAlert]:
    """Active alerts of the other side that fit this request and have not had it yet."""
    if item.status != RequestStatus.ACTIVE or not is_matchable(item) or not is_person_request(item):
        return []
    rows = CourierRouteAlert.objects.filter(
        active=True,
        role=search.opposite(item.type),
        origin_country=item.origin_country,
        origin_city=item.origin_city,
    ).exclude(sent__request=item)
    return [alert for alert in rows if matches(alert, item)]


def notify_new_request(request_id: int) -> int:
    """Message every fitting alert about a new request. Off (returns 0, records nothing) unless switched on."""
    if not conf.alerts_enabled():
        return 0
    item = ItemRequest.objects.select_related("user").filter(pk=request_id).first()
    if item is None:
        return 0
    sent = 0
    for alert in due_alerts(item):
        if _sent_last_day(alert.telegram_user_id) >= conf.alert_daily_cap():
            continue
        try:
            with transaction.atomic():
                CourierAlertSent.objects.create(alert=alert, request=item)
        except IntegrityError:
            continue
        if execute([send(alert.chat_id, *alert_message(alert, item))]):
            sent += 1
            CourierRouteAlert.objects.filter(pk=alert.pk).update(last_sent_at=timezone.now())
    return sent


def schedule_alerts(item: ItemRequest) -> None:
    """After the request is committed, look for alerts that want it."""
    if not item.pk:
        return
    request_id = item.pk

    def _run() -> None:
        try:
            notify_new_request(request_id)
        except Exception:  # noqa: BLE001 - an alert failure must never block saving a request
            logger.exception("Courier route alerts failed for request %s", request_id)

    transaction.on_commit(_run)
