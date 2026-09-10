from __future__ import annotations

import logging
from datetime import date

from django.conf import settings
from django.utils import timezone

from item_requests.models import ChannelStatus, City, ItemRequest, RequestStatus, RequestType
from matching.contact import CATEGORY_EMOJI, country_flag
from matching.models import MatchRating
from miniapp.catalog import format_month_day, format_route_text, ltr_embed, route_arrow
from miniapp.i18n import messages_for, t
from notifications.telegram import (
    edit_telegram_message,
    mini_app_in_chat_link,
    send_telegram_message_result,
)

logger = logging.getLogger(__name__)

CHANNEL_PUBLIC_RATING_MIN = 3


def channel_locale() -> str:
    return "fa"


def channel_chat_id() -> int | str | None:
    raw = (getattr(settings, "TELEGRAM_CHANNEL_ID", "") or "").strip()
    if raw:
        if raw.lstrip("-").isdigit():
            return _normalize_numeric_channel_id(int(raw))
        return raw if raw.startswith("@") else f"@{raw.lstrip('@')}"
    username = (getattr(settings, "TELEGRAM_CHANNEL_USERNAME", "") or "").strip().lstrip("@")
    if username:
        return f"@{username}"
    return None


def _normalize_numeric_channel_id(value: int) -> int:
    digits = str(abs(value))
    if value > 0 and digits.startswith("100") and len(digits) >= 12:
        return -value
    return value


def channel_enabled() -> bool:
    return bool(getattr(settings, "TELEGRAM_CHANNEL_ENABLED", False))


def view_on_koolbar_markup(item_request: ItemRequest) -> dict:
    messages = messages_for(channel_locale())
    return {
        "inline_keyboard": [
            [
                {
                    "text": t(messages, "channel.view"),
                    "url": mini_app_in_chat_link(f"explore_{item_request.pk}"),
                },
            ]
        ]
    }


def format_demand_message(item_request: ItemRequest, *, unavailable: bool = False) -> str:
    return _format_request_message(item_request, unavailable=unavailable)


def format_supply_message(item_request: ItemRequest, *, unavailable: bool = False) -> str:
    return _format_request_message(item_request, unavailable=unavailable)


def publish_demand_to_channel(item_request: ItemRequest) -> bool:
    return publish_request_to_channel(item_request)


def publish_supply_to_channel(item_request: ItemRequest) -> bool:
    return publish_request_to_channel(item_request)


def sync_request_channel(request_id: int) -> bool:
    try:
        item_request = (
            ItemRequest.objects.select_related("user")
            .prefetch_related("item_categories", "excluded_categories")
            .filter(pk=request_id)
            .first()
        )
        if item_request is None:
            return False
        return publish_request_to_channel(item_request)
    except Exception:
        logger.exception("Channel publication failed for request %s", request_id)
        _mark_failed(request_id)
        return False


def publish_request_to_channel(item_request: ItemRequest) -> bool:
    if not channel_enabled():
        return False
    chat_id = channel_chat_id()
    if not chat_id:
        logger.warning("Channel publication failed for request %s: channel id missing.", item_request.pk)
        _save_status(item_request, ChannelStatus.FAILED)
        return False

    unavailable = item_request.status != RequestStatus.ACTIVE
    if unavailable and not item_request.channel_message_id:
        return False

    text = _format_request_message(item_request, unavailable=unavailable)
    markup = {"inline_keyboard": []} if unavailable else view_on_koolbar_markup(item_request)

    if item_request.channel_message_id:
        edited = edit_telegram_message(
            chat_id,
            item_request.channel_message_id,
            text,
            reply_markup=markup,
        )
        if not edited:
            logger.error("Channel publication failed for request %s", item_request.pk)
            _save_status(item_request, ChannelStatus.FAILED)
            return False
        _save_status(item_request, ChannelStatus.UPDATED)
        return True

    if unavailable:
        return False

    result = send_telegram_message_result(chat_id, text, reply_markup=markup)
    message_id = result.get("message_id") if result else None
    if not message_id:
        logger.error("Channel publication failed for request %s", item_request.pk)
        _save_status(item_request, ChannelStatus.FAILED)
        return False
    item_request.channel_message_id = int(message_id)
    item_request.channel_published_at = timezone.now()
    item_request.channel_status = ChannelStatus.PUBLISHED
    item_request.save(update_fields=["channel_message_id", "channel_published_at", "channel_status", "updated_at"])
    return True


def publish_rating_to_channel(rating_id: int) -> bool:
    if not channel_enabled():
        return False
    chat_id = channel_chat_id()
    if not chat_id:
        return False
    rating = (
        MatchRating.objects.select_related("match__demand_request", "match__supply_request")
        .filter(pk=rating_id)
        .first()
    )
    if rating is None or rating.score < CHANNEL_PUBLIC_RATING_MIN:
        return False
    result = send_telegram_message_result(chat_id, _format_rating_message(rating))
    return bool(result and result.get("message_id"))


def _format_request_message(item_request: ItemRequest, *, unavailable: bool = False) -> str:
    locale = channel_locale()
    messages = messages_for(locale)
    origin = _flagged_city(item_request.origin_country, item_request.origin_city, locale)
    destinations = [
        _flagged_city(country, city, locale) for country, city in item_request.destination_stop_pairs()
    ] or [_flagged_city(item_request.destination_country, item_request.destination_city, locale)]
    title = t(messages, "channel.demandTitle" if item_request.type == RequestType.DEMAND else "channel.supplyTitle")
    route = ltr_embed(format_route_text(origin, destinations, locale))
    lines = [title, "", route, ""]
    if item_request.type == RequestType.DEMAND:
        desired = item_request.desired_date or item_request.date_from
        lines.append(f"📅 {_format_dates(desired, desired)}")
    else:
        if item_request.flight_date:
            lines.append(f"✈️ {_format_dates(item_request.flight_date, item_request.flight_date)}")
        lines.append(f"📅 {_format_dates(item_request.date_from, item_request.date_to)}")

    kg = _format_kg(item_request.weight_kg if item_request.type == RequestType.DEMAND else item_request.capacity_kg)
    if kg:
        if item_request.type == RequestType.DEMAND:
            lines.append(f"⚖️ {t(messages, 'channel.weight', kg=kg)}")
        else:
            lines.append(f"🧳 {t(messages, 'channel.capacity', kg=kg)}")

    allowed = [_category_line(category, locale) for category in item_request.item_categories.order_by("sort_order")]
    excluded = [_category_line(category, locale) for category in item_request.excluded_categories.order_by("sort_order")]
    extra = (item_request.excluded_other_text or "").strip()

    if item_request.type == RequestType.DEMAND and allowed:
        lines.append("\n".join(allowed))
    elif item_request.type == RequestType.SUPPLY:
        if allowed:
            lines.extend(["", t(messages, "channel.canCarry"), *allowed])
        if excluded or extra:
            lines.extend(["", t(messages, "channel.willNotCarry"), *excluded])
            if extra:
                lines.append(f"📦 {extra}")

    if unavailable:
        lines = [t(messages, "channel.unavailable"), ""] + lines
    return "\n".join(lines)


def _format_rating_message(rating: MatchRating) -> str:
    locale = channel_locale()
    messages = messages_for(locale)
    demand = rating.match.demand_request
    supply = rating.match.supply_request
    origin = _flagged_city(demand.origin_country, demand.origin_city, locale)
    destination = _flagged_city(demand.destination_country, demand.destination_city, locale)
    stars = ("★" * rating.score) + ("☆" * (5 - rating.score))
    lines = [
        t(messages, "channel.ratingTitle"),
        "",
        ltr_embed(format_route_text(origin, [destination], locale)),
        "",
    ]
    if supply.flight_date:
        lines.append(f"✈️ {_format_dates(supply.flight_date, supply.flight_date)}")
        lines.append("")
    lines.append(f"{stars}  {rating.score}/5")
    comment = (rating.comment or "").strip()
    if comment:
        lines.extend(["", comment])
    return "\n".join(lines)


def _flagged_city(country_code: str, slug: str, locale: str) -> str:
    city = (
        City.objects.filter(country__code=country_code, slug=slug, is_active=True)
        .only("name_en", "name_fa")
        .first()
    )
    if city:
        name = city.name_fa if locale == "fa" else city.name_en
    else:
        name = slug.replace("-", " ").title()
    flag = country_flag(country_code)
    return f"{flag} {name}".strip() if flag else name


def _category_line(category, locale: str) -> str:
    emoji = CATEGORY_EMOJI.get(category.code, "📦")
    name = category.name_fa if locale == "fa" else category.name_en
    return f"{emoji} {name}"


def _format_dates(start: date, end: date) -> str:
    start_label = format_month_day(start, channel_locale())
    if start == end:
        return start_label
    return ltr_embed(f"{start_label} {route_arrow()} {format_month_day(end, channel_locale())}")


def _format_kg(value) -> str | None:
    if value is None:
        return None
    amount = float(value)
    if amount.is_integer():
        return str(int(amount))
    return f"{amount:.2f}".rstrip("0").rstrip(".")


def _save_status(item_request: ItemRequest, status: str) -> None:
    item_request.channel_status = status
    item_request.save(update_fields=["channel_status", "updated_at"])


def _mark_failed(request_id: int) -> None:
    ItemRequest.objects.filter(pk=request_id).update(channel_status=ChannelStatus.FAILED, updated_at=timezone.now())
