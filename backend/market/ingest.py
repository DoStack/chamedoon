from __future__ import annotations

import logging
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from typing import Callable

from django.conf import settings
from django.utils import timezone

from market.classify import classify_role, extract_route, extract_weight_kg
from market.models import MarketIngestState, MarketPost
from market.parse import parse_preview_html

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; KoolbarMarketBot/1.0; +https://github.com/mohammadisaeedir/koolbar)"
PREVIEW_URL = "https://t.me/s/{username}"
HEAD_PAGES = 3
BACKFILL_PAGES = 5
LOOKBACK_PAGES = 80
BACKFILL_DAYS = 30
MIN_LOOKBACK_DAYS = 1
MAX_LOOKBACK_DAYS = BACKFILL_DAYS
DEFAULT_EXTRACT_DAYS = 1
INGEST_BUDGET_SECONDS = 50
PREVIEW_TIMEOUT_SECONDS = 8
STOP_RESERVE_SECONDS = 1.5


def clamp_lookback_days(value, *, default: int = BACKFILL_DAYS) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        days = default
    return max(MIN_LOOKBACK_DAYS, min(MAX_LOOKBACK_DAYS, days))


def market_channel_username() -> str:
    names = market_channel_usernames()
    return names[0] if names else ""


def market_channel_usernames() -> list[str]:
    raw = (getattr(settings, "MARKET_CHANNEL_USERNAMES", "") or "").strip()
    names = [item.strip().lstrip("@") for item in raw.split(",") if item.strip()]
    if names:
        return list(dict.fromkeys(names))
    single = (getattr(settings, "MARKET_CHANNEL_USERNAME", "") or "").strip().lstrip("@")
    return [single] if single else []


def market_ingest_enabled() -> bool:
    return bool(getattr(settings, "MARKET_INGEST_ENABLED", True)) and bool(market_channel_usernames())


def _cutoff(days: int | None = None):
    window = BACKFILL_DAYS if days is None else clamp_lookback_days(days)
    return timezone.now() - timedelta(days=window)


def _should_stop(stop_at: float | None) -> bool:
    return stop_at is not None and time.monotonic() + STOP_RESERVE_SECONDS >= stop_at


def oldest_posted_at(username: str):
    return (
        MarketPost.objects.filter(channel_username=username)
        .order_by("posted_at")
        .values_list("posted_at", flat=True)
        .first()
    )


def lookback_days_for(days: int | None) -> int:
    return BACKFILL_DAYS if days is None else clamp_lookback_days(days)


def lookback_complete(username: str, days: int | None) -> bool:
    lookback = lookback_days_for(days)
    if lookback <= 1:
        return True
    state = MarketIngestState.objects.filter(channel_username=username).first()
    return bool(state and state.lookback_days == lookback and state.backfill_complete)


def pending_post_count(*, days: int | None = None, cutoff=None) -> int:
    posts = MarketPost.objects.filter(item_request__isnull=True, skip_reason="")
    if cutoff is None and days is not None:
        cutoff = _cutoff(days)
    if cutoff is not None:
        posts = posts.filter(posted_at__gte=cutoff)
    return posts.count()


def _usernames_by_priority(days=None) -> list[str]:
    names = market_channel_usernames()
    never = datetime(1970, 1, 1, tzinfo=dt_timezone.utc)
    states = {
        row.channel_username: row.last_run_at
        for row in MarketIngestState.objects.filter(channel_username__in=names)
    }

    def sort_key(name: str):
        behind = not lookback_complete(name, days)
        return (not behind, states.get(name) or never)

    return sorted(names, key=sort_key)


def fetch_preview_page(username: str, before: int | None = None) -> str:
    url = PREVIEW_URL.format(username=username)
    if before:
        url = f"{url}?before={before}"
    return _fetch_preview_url(url)


def fetch_preview_around(username: str, message_id: int) -> str:
    username = (username or "").strip().lstrip("@")
    return _fetch_preview_url(f"{PREVIEW_URL.format(username=username)}/{int(message_id)}")


def _fetch_preview_url(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en,fa"},
    )
    with urllib.request.urlopen(request, timeout=PREVIEW_TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", "replace")


def _upsert_post(raw: dict, *, cutoff, ignore_cutoff: bool = False) -> tuple[MarketPost, bool] | None:
    posted_at = raw["posted_at"]
    if not ignore_cutoff and posted_at < cutoff:
        return None
    origin, dest = extract_route(raw["text"])
    username = raw["channel_username"]
    message_id = raw["telegram_message_id"]
    weight = extract_weight_kg(raw["text"])
    defaults = {
        "posted_at": raw["posted_at"],
        "text": raw["text"],
        "views": raw.get("views"),
        "has_photo": bool(raw.get("has_photo")),
        "role": classify_role(raw["text"]),
        "origin_city": (origin or {}).get("city", ""),
        "origin_country": (origin or {}).get("country", ""),
        "destination_city": (dest or {}).get("city", ""),
        "destination_country": (dest or {}).get("country", ""),
        "weight_kg": Decimal(str(weight)) if weight is not None else None,
        "source_url": f"https://t.me/{username}/{message_id}",
        "author_name": (raw.get("author_name") or "")[:128],
        "author_username": (raw.get("author_username") or "")[:64],
    }
    return MarketPost.objects.update_or_create(
        channel_username=username,
        telegram_message_id=message_id,
        defaults=defaults,
    )


def _scan_pages(
    username: str,
    *,
    before: int | None,
    max_pages: int,
    stop_when_known: bool,
    fetch_page: Callable[[str, int | None], str],
    cutoff,
    stop_at: float | None = None,
) -> tuple[int, int, int, int | None]:
    created = updated = 0
    oldest_id = before
    pages = 0
    cursor = before
    hit_cutoff = False
    for _ in range(max_pages):
        if _should_stop(stop_at):
            break
        html = fetch_page(username, cursor)
        posts = parse_preview_html(html, default_username=username)
        pages += 1
        if not posts:
            hit_cutoff = True
            break
        known_on_page = 0
        reached_cutoff = False
        for raw in posts:
            if raw["posted_at"] < cutoff:
                reached_cutoff = True
                continue
            result = _upsert_post(raw, cutoff=cutoff)
            if result is None:
                reached_cutoff = True
                continue
            _obj, was_created = result
            if was_created:
                created += 1
            else:
                updated += 1
                known_on_page += 1
        ids = [item["telegram_message_id"] for item in posts]
        oldest_id = min(ids)
        if reached_cutoff:
            hit_cutoff = True
            break
        if stop_when_known and known_on_page == len(posts):
            break
        cursor = oldest_id
        if cursor == before:
            break
        before = cursor
    return created, updated, pages, oldest_id, hit_cutoff


def ingest_market_channel(
    *,
    username: str | None = None,
    fetch_page: Callable[[str, int | None], str] | None = None,
    head_pages: int | None = None,
    backfill_pages: int | None = None,
    days: int | None = None,
    stop_at: float | None = None,
) -> dict:
    username = (username or market_channel_username()).lstrip("@")
    if not username:
        return {"ok": False, "error": "channel username missing", "created": 0, "updated": 0, "pages": 0}

    fetch = fetch_page or fetch_preview_page
    cutoff = _cutoff(days)
    lookback = lookback_days_for(days)
    catchup = days is not None and lookback > 1
    state, _ = MarketIngestState.objects.get_or_create(channel_username=username)
    created = updated = pages = 0
    try:
        if catchup and state.lookback_days != lookback:
            state.lookback_before_id = None
            state.lookback_days = lookback
            state.backfill_complete = False
        if _should_stop(stop_at):
            return {
                "ok": True,
                "channel": username,
                "deferred": True,
                "created": 0,
                "updated": 0,
                "pages": 0,
                "window_covered": lookback_complete(username, days),
            }
        head_created, head_updated, head_pages_used, head_oldest, head_hit_cutoff = _scan_pages(
            username,
            before=None,
            max_pages=head_pages if head_pages is not None else 1,
            stop_when_known=True,
            fetch_page=fetch,
            cutoff=cutoff,
            stop_at=stop_at,
        )
        created += head_created
        updated += head_updated
        pages += head_pages_used
        hit_cutoff = head_hit_cutoff

        if catchup and not state.backfill_complete and not hit_cutoff and not _should_stop(stop_at):
            if backfill_pages is not None:
                max_back = backfill_pages
            elif stop_at is not None:
                max_back = LOOKBACK_PAGES
            else:
                max_back = BACKFILL_PAGES
            cursor = state.lookback_before_id or head_oldest
            if cursor and max_back:
                back_created, back_updated, back_pages_used, back_oldest, back_hit_cutoff = _scan_pages(
                    username,
                    before=cursor,
                    max_pages=max_back,
                    stop_when_known=False,
                    fetch_page=fetch,
                    cutoff=cutoff,
                    stop_at=stop_at,
                )
                created += back_created
                updated += back_updated
                pages += back_pages_used
                hit_cutoff = back_hit_cutoff
                if back_oldest:
                    state.lookback_before_id = back_oldest
            elif not cursor:
                hit_cutoff = True

        newest_id = (
            MarketPost.objects.filter(channel_username=username)
            .order_by("-telegram_message_id")
            .values_list("telegram_message_id", flat=True)
            .first()
        )
        oldest_id = (
            MarketPost.objects.filter(channel_username=username)
            .order_by("telegram_message_id")
            .values_list("telegram_message_id", flat=True)
            .first()
        )
        if catchup:
            state.lookback_days = lookback
            state.backfill_complete = bool(hit_cutoff)
            if state.backfill_complete:
                state.lookback_before_id = None
            elif state.lookback_before_id is None and head_oldest:
                state.lookback_before_id = head_oldest
        else:
            state.backfill_complete = True
        state.newest_message_id = newest_id
        state.oldest_message_id = oldest_id
        state.last_run_at = timezone.now()
        state.last_created = created
        state.last_updated = updated
        state.last_error = ""
        state.save()
        return {
            "ok": True,
            "channel": username,
            "created": created,
            "updated": updated,
            "pages": pages,
            "newest_message_id": newest_id,
            "oldest_message_id": oldest_id,
            "backfill_complete": state.backfill_complete,
            "window_covered": lookback_complete(username, days) if catchup else True,
            "oldest_posted_at": oldest_posted_at(username),
            "post_count": MarketPost.objects.filter(channel_username=username).count(),
        }
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("Market channel ingest skipped for %s: %s", username, exc)
        state.last_run_at = timezone.now()
        state.last_error = str(exc)[:2000]
        state.save(update_fields=["last_run_at", "last_error"])
        return {
            "ok": True,
            "skipped": True,
            "channel": username,
            "error": str(exc),
            "created": created,
            "updated": updated,
            "pages": pages,
        }
    except Exception as exc:
        logger.exception("Market channel ingest failed for %s", username)
        state.last_run_at = timezone.now()
        state.last_error = str(exc)[:2000]
        state.save(update_fields=["last_run_at", "last_error"])
        return {"ok": False, "error": str(exc), "created": created, "updated": updated, "pages": pages}


def ingest_all_market_channels(
    *,
    fetch_page: Callable[[str, int | None], str] | None = None,
    head_pages: int | None = None,
    backfill_pages: int | None = None,
    budget_seconds: float | None = None,
    days: int | None = None,
    stop_at: float | None = None,
) -> dict:
    channels = []
    created = updated = pages = 0
    if budget_seconds is not None:
        deadline = time.monotonic() + max(1.0, float(budget_seconds))
    elif fetch_page is not None:
        deadline = None
    else:
        deadline = time.monotonic() + INGEST_BUDGET_SECONDS
    if stop_at is not None:
        deadline = stop_at if deadline is None else min(deadline, stop_at)
    truncated = False
    for username in _usernames_by_priority(days):
        if deadline is not None and _should_stop(deadline):
            truncated = True
            channels.append(
                {
                    "ok": True,
                    "channel": username,
                    "deferred": True,
                    "created": 0,
                    "updated": 0,
                    "pages": 0,
                }
            )
            continue
        result = ingest_market_channel(
            username=username,
            fetch_page=fetch_page,
            head_pages=head_pages,
            backfill_pages=backfill_pages,
            days=days,
            stop_at=deadline,
        )
        if result.get("deferred"):
            truncated = True
        channels.append(result)
        created += int(result.get("created") or 0)
        updated += int(result.get("updated") or 0)
        pages += int(result.get("pages") or 0)
    return {
        "ok": True,
        "created": created,
        "updated": updated,
        "pages": pages,
        "truncated": truncated,
        "channels": channels,
    }
