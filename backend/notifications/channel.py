from __future__ import annotations

import logging
import time
from datetime import date

from django.conf import settings
from django.utils import timezone

from item_requests.models import ChannelStatus, City, ItemRequest, RequestStatus, RequestType
from matching.contact import country_flag
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
    targets = channel_chat_targets()
    return targets[0] if targets else None


def channel_chat_targets() -> list[int | str]:
    targets: list[int | str] = []
    raw = (getattr(settings, "TELEGRAM_CHANNEL_ID", "") or "").strip()
    if raw:
        if raw.lstrip("-").isdigit():
            targets.append(_normalize_numeric_channel_id(int(raw)))
        else:
            targets.append(raw if raw.startswith("@") else f"@{raw.lstrip('@')}")
    username = (getattr(settings, "TELEGRAM_CHANNEL_USERNAME", "") or "").strip().lstrip("@")
    if username:
        handle = f"@{username}"
        if handle not in {str(item) for item in targets}:
            targets.append(handle)
    return targets


def _normalize_numeric_channel_id(value: int) -> int:
    digits = str(abs(value))
    if value > 0 and digits.startswith("100") and len(digits) >= 12:
        return -value
    return value


def channel_enabled() -> bool:
    return bool(getattr(settings, "TELEGRAM_CHANNEL_ENABLED", False))


def view_on_chamedoon_markup(item_request: ItemRequest) -> dict:
    messages = messages_for(channel_locale())
    # Same URL shape as CA/Germany: t.me/<bot>?startapp=… (Main Mini App over the channel).
    # Courier bot only — never chamedoon_home_bot.
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


def refresh_recent_channel_buttons(
    *,
    limit: int = 40,
    message_ids: list[int] | None = None,
) -> int:
    """Put the opening Mini App link on buttons already sitting under channel posts."""
    from notifications.telegram import call_telegram_api

    targets = channel_chat_targets()
    if not targets or not channel_enabled():
        return 0
    rows = ItemRequest.objects.filter(
        status=RequestStatus.ACTIVE,
        channel_message_id__isnull=False,
    )
    if message_ids:
        rows = rows.filter(channel_message_id__in=message_ids)
    else:
        rows = rows.order_by("-channel_published_at", "-id")[:limit]
    updated = 0
    for item in rows:
        markup = view_on_chamedoon_markup(item)
        for chat in targets:
            result = call_telegram_api(
                "editMessageReplyMarkup",
                {
                    "chat_id": chat,
                    "message_id": int(item.channel_message_id),
                    "reply_markup": markup,
                },
            )
            if result is not None:
                updated += 1
                break
    return updated


def format_demand_message(item_request: ItemRequest, *, unavailable: bool = False) -> str:
    return _format_request_message(item_request, unavailable=unavailable)


def format_supply_message(item_request: ItemRequest, *, unavailable: bool = False) -> str:
    return _format_request_message(item_request, unavailable=unavailable)


def publish_demand_to_channel(item_request: ItemRequest) -> bool:
    return publish_request_to_channel(item_request)


def publish_supply_to_channel(item_request: ItemRequest) -> bool:
    return publish_request_to_channel(item_request)


PUBLISH_BATCH = 10


def unpublished_imported_queryset():
    return ItemRequest.objects.filter(
        imported=True,
        status=RequestStatus.ACTIVE,
        channel_message_id__isnull=True,
    ).order_by("id")


def unpublished_imported_count() -> int:
    return unpublished_imported_queryset().count()


def publish_unpublished_imported(*, limit: int = PUBLISH_BATCH, stop_at: float | None = None) -> dict:
    published = failed = 0
    truncated = False
    processed = 0
    for item in unpublished_imported_queryset().iterator():
        if stop_at is not None and time.monotonic() >= stop_at:
            truncated = True
            break
        if processed >= limit:
            truncated = unpublished_imported_queryset().exists()
            break
        processed += 1
        if sync_request_channel(item.pk):
            published += 1
        else:
            failed += 1
    return {
        "ok": True,
        "published": published,
        "failed": failed,
        "truncated": truncated,
        "remaining": unpublished_imported_count(),
    }


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
    markup = {"inline_keyboard": []} if unavailable else view_on_chamedoon_markup(item_request)
    targets = channel_chat_targets()

    if item_request.channel_message_id:
        edited = False
        for chat_id in targets:
            edited = edit_telegram_message(
                chat_id,
                item_request.channel_message_id,
                text,
                reply_markup=markup,
            )
            if edited:
                break
        if not edited:
            logger.error(
                "Channel publication failed for request %s (chat_id=%s). "
                "Add the bot as an administrator of the channel.",
                item_request.pk,
                targets,
            )
            _save_status(item_request, ChannelStatus.FAILED)
            return False
        _save_status(item_request, ChannelStatus.UPDATED)
        return True

    if unavailable:
        return False

    result = None
    for chat_id in targets:
        result = send_telegram_message_result(chat_id, text, reply_markup=markup)
        if result and result.get("message_id"):
            break
        logger.warning(
            "Channel send failed for request %s via %s. "
            "If this is a new bot, add it as a channel admin.",
            item_request.pk,
            chat_id,
        )
    message_id = result.get("message_id") if result else None
    if not message_id:
        logger.error(
            "Channel publication failed for request %s (chat_id=%s). "
            "Add the bot as an administrator of the channel.",
            item_request.pk,
            targets,
        )
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
    """Courier post for the main and the country channels (design «باربری ۲»).

    Role and route first, then date, weight, a prohibited-items hint, cost «توافقی» and the
    hashtags #مبدا_مقصد #مسافر|#بسته #ماه, so anyone can tap their own route. Plain text, no names.
    """
    from courier import texts

    messages = messages_for(channel_locale())
    supply = item_request.type == RequestType.SUPPLY
    stops = item_request.destination_stop_pairs() or [(item_request.destination_country, item_request.destination_city)]
    origin = texts.city_name(item_request.origin_country, item_request.origin_city)
    destinations = [texts.city_name(country, city) for country, city in stops]
    role = "🧳 مسافر" if supply else "📦 بسته دارم"
    lines = [f"{role} · {origin} {texts.ARROW} {' و '.join(destinations)}"]

    if supply:
        day = item_request.flight_date or item_request.date_to
        date_line = f"📅 {texts.day_month(day)}" if day else ""
    elif item_request.desired_date:
        day = item_request.desired_date
        date_line = f"📅 {texts.day_month(day)}"
    else:
        day = item_request.date_to
        date_line = f"📅 تا {texts.day_month(day)}" if day else ""
    if date_line:
        lines.append(date_line)

    kg = texts.kg_text(item_request.capacity_kg if supply else item_request.weight_kg)
    what = " و ".join(category.name_fa for category in item_request.item_categories.order_by("sort_order"))
    weight = f"جا: {kg}" if supply and kg else kg
    weight_line = " · ".join(part for part in (weight, what) if part)
    if weight_line:
        lines.append(f"⚖️ {weight_line}")

    excluded = [category.name_fa for category in item_request.excluded_categories.order_by("sort_order")]
    extra = (item_request.excluded_other_text or "").strip()
    if extra:
        excluded.append(extra)
    lines.append(f"🚫 {' و '.join(excluded)} نه" if excluded else "🚫 اقلام ممنوعه نه (فهرست در ربات)")
    lines.append(texts.COST)

    tags = [f"#{texts.hashtag_word(origin)}_{texts.hashtag_word(name)}" for name in destinations[:3]]
    tags.append("#مسافر" if supply else "#بسته")
    if day:
        tags.append(f"#{texts.month_name(day)}")
    lines.append(" ".join(tags))

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
        format_route_text(origin, [destination], locale),
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


def _format_dates(start: date, end: date) -> str:
    start_label = format_month_day(start, channel_locale())
    if start == end:
        return start_label
    return ltr_embed(f"{start_label} {route_arrow(channel_locale())} {format_month_day(end, channel_locale())}")


def _save_status(item_request: ItemRequest, status: str) -> None:
    item_request.channel_status = status
    item_request.save(update_fields=["channel_status", "updated_at"])


def _mark_failed(request_id: int) -> None:
    ItemRequest.objects.filter(pk=request_id).update(channel_status=ChannelStatus.FAILED, updated_at=timezone.now())
