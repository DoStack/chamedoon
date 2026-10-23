"""Post courier (باربری) requests to the per-country courier channels, in addition to the main channel.

A request goes to the channel of every country its route touches (origin, destination or a
destination stop; Toronto→Tehran goes to CA). The post is the main channel post: same text,
same button, same courier bot. Each (request, channel) pair is recorded in CourierPublication,
so nothing posts twice. A channel only gets requests created after its active_from and within
the lookback window, a few per run, up to a daily cap. Failed sends are retried on later runs
up to MAX_ATTEMPTS. Housing channels are refused here, as the courier channels are refused by
housing (home/guard.py).
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from django.apps import apps
from django.conf import settings
from django.db.models import F, Q
from django.utils import timezone

from item_requests.models import CourierChannel, CourierPublication, ItemRequest, RequestStatus
from notifications.channel import (
    _format_request_message,
    channel_chat_targets,
    channel_enabled,
    view_on_chamedoon_markup,
)
from notifications import telegram

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
CLAIM_SECONDS = 300
PER_POST_RESERVE_SECONDS = 4


class SendError(Exception):
    def __init__(self, description: str, retry_after: int = 0) -> None:
        super().__init__(description)
        self.description = description[:300]
        self.retry_after = retry_after


def _api(method: str, payload: dict) -> tuple[dict | None, dict]:
    """One courier bot call. Goes through call_telegram_api so tests and mocks of the main channel apply here too."""
    telegram.reset_last_telegram_error()
    body = telegram.call_telegram_api(method, payload)
    return body, ({} if body is not None else telegram.last_telegram_error())


def daily_cap() -> int:
    return max(1, int(getattr(settings, "COURIER_COUNTRY_CHANNEL_DAILY_CAP", 40) or 40))


def lookback_days() -> int:
    return max(1, int(getattr(settings, "COURIER_COUNTRY_CHANNEL_LOOKBACK_DAYS", 10) or 10))


def batch_size() -> int:
    return max(1, int(getattr(settings, "COURIER_COUNTRY_CHANNEL_BATCH", 10) or 10))


def _digits(value) -> str:
    return str(value or "").strip().lstrip("-").removeprefix("100")


def housing_channel_names() -> set[str]:
    """Usernames and ids of every housing channel. Looked up by app label: courier code never imports housing."""
    try:
        HomeChannel = apps.get_model("home", "HomeChannel")
    except LookupError:  # pragma: no cover - the home app is always installed
        return set()
    found: set[str] = set()
    for username, chat_id in HomeChannel.objects.values_list("username", "chat_id"):
        if username:
            found.add(username.strip().lstrip("@").lower())
        if chat_id:
            found.add(_digits(chat_id))
    return found


def blocked_reason(channel: CourierChannel) -> str:
    """Why courier must not post to this channel, or an empty string."""
    housing = housing_channel_names()
    if channel.username and channel.username.lower() in housing:
        return "This is a housing channel. Courier never posts there."
    if channel.chat_id and _digits(channel.chat_id) in housing:
        return "This is a housing channel. Courier never posts there."
    for main in channel_chat_targets():
        main_text = str(main).lstrip("@").lower()
        if channel.username and channel.username.lower() == main_text:
            return "This is the main courier channel. It already gets every request."
        if channel.chat_id and main_text.lstrip("-").isdigit() and _digits(channel.chat_id) == _digits(main_text):
            return "This is the main courier channel. It already gets every request."
    if channel.target() is None:
        return "No chat id or username."
    return ""


def ready() -> str:
    """Empty when posting can run, else why not."""
    if not channel_enabled():
        return "courier channel publishing is off (TELEGRAM_CHANNEL_ENABLED)"
    if not (getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "").strip():
        return "courier bot token missing"
    return ""


def enabled_channels() -> list[CourierChannel]:
    return list(CourierChannel.objects.filter(enabled=True))


def route(item_request: ItemRequest, channels: list[CourierChannel]) -> list[CourierChannel]:
    """The country channels of a request: every enabled channel whose country is on its route."""
    countries = item_request.route_countries()
    return [channel for channel in channels if channel.country in countries]


def eligible(item_request: ItemRequest, channel: CourierChannel, *, now=None) -> bool:
    now = now or timezone.now()
    if item_request.status != RequestStatus.ACTIVE:
        return False
    if channel.active_from and item_request.created_at < channel.active_from:
        return False
    return item_request.created_at >= now - timedelta(days=lookback_days())


def country_publishable(channels: list[CourierChannel] | None = None) -> list[tuple[ItemRequest, CourierChannel]]:
    """(request, channel) pairs still to post, oldest request first."""
    channels = enabled_channels() if channels is None else channels
    channels = [channel for channel in channels if not blocked_reason(channel)]
    if not channels:
        return []
    now = timezone.now()
    done = set(
        CourierPublication.objects.filter(
            Q(published_at__isnull=False) | Q(attempts__gte=MAX_ATTEMPTS), channel__in=channels
        ).values_list("request_id", "channel_id")
    )
    candidates = (
        ItemRequest.objects.filter(status=RequestStatus.ACTIVE, created_at__gte=now - timedelta(days=lookback_days()))
        .select_related("user")
        .prefetch_related("item_categories", "excluded_categories")
        .order_by("created_at", "id")
    )
    rows = []
    for item_request in candidates:
        for channel in route(item_request, channels):
            if (item_request.pk, channel.pk) in done or not eligible(item_request, channel, now=now):
                continue
            rows.append((item_request, channel))
    return rows


def _send(item_request: ItemRequest, channel: CourierChannel) -> list[int]:
    """Send the main channel post to one country channel. Returns the message ids. Raises SendError."""
    problem = blocked_reason(channel)
    if problem:
        raise SendError(problem)
    payload = {
        "chat_id": channel.target(),
        "text": _format_request_message(item_request),
        "reply_markup": view_on_chamedoon_markup(item_request),
    }
    body, error = _api("sendMessage", payload)
    message_id = ((body or {}).get("result") or {}).get("message_id")
    if not message_id:
        retry_after = int(((error or {}).get("parameters") or {}).get("retry_after") or 0)
        raise SendError(str((error or {}).get("description") or "sendMessage failed"), retry_after=retry_after)
    return [int(message_id)]


def _claim(row: CourierPublication) -> bool:
    """Mark the pair as being sent. False when another run has it or it is already posted."""
    now = timezone.now()
    return bool(
        CourierPublication.objects.filter(pk=row.pk, published_at__isnull=True, attempts__lt=MAX_ATTEMPTS)
        .filter(Q(claimed_at__isnull=True) | Q(claimed_at__lt=now - timedelta(seconds=CLAIM_SECONDS)))
        .update(claimed_at=now)
    )


def publish_to_country(item_request: ItemRequest, channel: CourierChannel) -> bool:
    """Post one request to one country channel and record it. False when it was already posted or taken.

    Raises SendError after recording the failed attempt.
    """
    row, _created = CourierPublication.objects.get_or_create(request=item_request, channel=channel)
    if not _claim(row):
        return False
    try:
        ids = _send(item_request, channel)
    except SendError as exc:
        CourierPublication.objects.filter(pk=row.pk).update(
            attempts=F("attempts") + 1, error=exc.description, claimed_at=None
        )
        CourierChannel.objects.filter(pk=channel.pk).update(last_error=exc.description)
        raise
    CourierPublication.objects.filter(pk=row.pk).update(
        message_ids=ids, published_at=timezone.now(), error="", claimed_at=None
    )
    return True


def _posted_last_day(channel: CourierChannel) -> int:
    return CourierPublication.objects.filter(
        channel=channel, published_at__gte=timezone.now() - timedelta(days=1)
    ).count()


def publish_country_channels(*, limit: int | None = None, stop_at: float | None = None) -> dict:
    """Post the next requests to their country channels. Never raises on Telegram trouble."""
    counts = {"ok": True, "published": 0, "failed": 0, "over_daily_cap": 0, "closed": 0, "retry_after": 0}
    blocker = ready()
    if blocker:
        counts["skipped"] = blocker
        return counts
    limit = limit or batch_size()
    posted_today: dict[int, int] = {}
    for item_request, channel in country_publishable():
        if counts["published"] + counts["failed"] >= limit:
            break
        if stop_at is not None and time.monotonic() + PER_POST_RESERVE_SECONDS >= stop_at:
            break
        if channel.pk not in posted_today:
            posted_today[channel.pk] = _posted_last_day(channel)
        if posted_today[channel.pk] >= daily_cap():
            counts["over_daily_cap"] += 1
            continue
        try:
            sent = publish_to_country(item_request, channel)
        except SendError as exc:
            counts["failed"] += 1
            logger.warning(
                "Courier country publish failed for request %s to %s: %s", item_request.pk, channel.country, exc.description
            )
            if exc.retry_after:
                counts["retry_after"] = exc.retry_after
                break
            continue
        if sent:
            posted_today[channel.pk] += 1
            counts["published"] += 1
    if not counts["retry_after"]:
        counts["closed"] = close_unavailable_posts(stop_at=stop_at)
    counts["remaining"] = len(country_publishable())
    return counts


def close_unavailable_posts(*, limit: int = 20, stop_at: float | None = None) -> int:
    """Mark country posts of requests that are no longer active as unavailable, like the main channel does."""
    rows = (
        CourierPublication.objects.filter(published_at__isnull=False, closed_at__isnull=True)
        .exclude(request__status=RequestStatus.ACTIVE)
        .select_related("request", "channel")
        .order_by("published_at")[:limit]
    )
    closed = 0
    for row in rows:
        if stop_at is not None and time.monotonic() + PER_POST_RESERVE_SECONDS >= stop_at:
            break
        if not row.message_ids or blocked_reason(row.channel):
            continue
        payload = {
            "chat_id": row.channel.target(),
            "message_id": int(row.message_ids[0]),
            "text": _format_request_message(row.request, unavailable=True),
            "reply_markup": {"inline_keyboard": []},
        }
        body, error = _api("editMessageText", payload)
        description = str((error or {}).get("description") or "")
        if body is not None or "not modified" in description or "not found" in description:
            CourierPublication.objects.filter(pk=row.pk).update(closed_at=timezone.now())
            closed += 1
    return closed


def verify_courier_channel(channel: CourierChannel) -> str:
    """Check the courier bot is an admin who can post here. Saves chat id and title. Empty string or the problem."""
    problem = blocked_reason(channel)
    if problem:
        channel.last_error = problem
        channel.save(update_fields=["last_error"])
        return problem

    def call(method: str, payload: dict) -> dict:
        body, error = _api(method, payload)
        if body is None:
            raise SendError(str((error or {}).get("description") or f"{method} failed"))
        return body.get("result") or {}

    try:
        chat = call("getChat", {"chat_id": channel.target()})
        me = call("getMe", {})
        member = call("getChatMember", {"chat_id": chat["id"], "user_id": me["id"]})
        status = member.get("status")
        if status not in {"administrator", "creator"}:
            raise SendError(f"@{me.get('username')} is not an admin of this channel")
        if status == "administrator" and not member.get("can_post_messages", False):
            raise SendError(f"@{me.get('username')} cannot post messages in this channel")
    except (SendError, KeyError) as exc:
        channel.last_error = (getattr(exc, "description", "") or f"unexpected answer: {exc}")[:300]
        channel.save(update_fields=["last_error"])
        return channel.last_error
    probe = CourierChannel(pk=channel.pk, country=channel.country, username=channel.username, chat_id=int(chat["id"]))
    problem = blocked_reason(probe)
    if problem:
        channel.last_error = problem
        channel.save(update_fields=["last_error"])
        return problem
    channel.chat_id = int(chat["id"])
    channel.title = str(chat.get("title") or channel.title)[:128]
    if chat.get("username") and not channel.username:
        channel.username = str(chat["username"])
    channel.verified_at = timezone.now()
    channel.last_error = ""
    channel.save(update_fields=["chat_id", "title", "username", "verified_at", "last_error"])
    return ""
