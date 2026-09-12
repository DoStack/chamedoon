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
BACKFILL_DAYS = 30
INGEST_BUDGET_SECONDS = 50


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


def _cutoff():
    return timezone.now() - timedelta(days=BACKFILL_DAYS)


def _usernames_by_priority() -> list[str]:
    names = market_channel_usernames()
    never = datetime(1970, 1, 1, tzinfo=dt_timezone.utc)
    states = {
        row.channel_username: row.last_run_at
        for row in MarketIngestState.objects.filter(channel_username__in=names)
    }
    return sorted(names, key=lambda name: states.get(name) or never)


def fetch_preview_page(username: str, before: int | None = None) -> str:
    url = PREVIEW_URL.format(username=username)
    if before:
        url = f"{url}?before={before}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en,fa"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", "replace")


def _upsert_post(raw: dict, *, cutoff) -> tuple[MarketPost, bool] | None:
    posted_at = raw["posted_at"]
    if posted_at < cutoff:
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
) -> tuple[int, int, int, int | None]:
    created = updated = 0
    oldest_id = before
    pages = 0
    cursor = before
    hit_cutoff = False
    for _ in range(max_pages):
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
) -> dict:
    username = (username or market_channel_username()).lstrip("@")
    if not username:
        return {"ok": False, "error": "channel username missing", "created": 0, "updated": 0, "pages": 0}

    fetch = fetch_page or fetch_preview_page
    cutoff = _cutoff()
    state, _ = MarketIngestState.objects.get_or_create(channel_username=username)
    created = updated = pages = 0
    try:
        head_created, head_updated, head_pages_used, _oldest, head_hit_cutoff = _scan_pages(
            username,
            before=None,
            max_pages=head_pages if head_pages is not None else HEAD_PAGES,
            stop_when_known=True,
            fetch_page=fetch,
            cutoff=cutoff,
        )
        created += head_created
        updated += head_updated
        pages += head_pages_used
        if head_hit_cutoff:
            state.backfill_complete = True

        oldest_id_stored = (
            MarketPost.objects.filter(channel_username=username)
            .order_by("telegram_message_id")
            .values_list("telegram_message_id", flat=True)
            .first()
        )
        if not state.backfill_complete:
            back_created, back_updated, back_pages_used, _, back_hit_cutoff = _scan_pages(
                username,
                before=oldest_id_stored,
                max_pages=backfill_pages if backfill_pages is not None else BACKFILL_PAGES,
                stop_when_known=False,
                fetch_page=fetch,
                cutoff=cutoff,
            )
            created += back_created
            updated += back_updated
            pages += back_pages_used
            if back_hit_cutoff:
                state.backfill_complete = True

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
) -> dict:
    channels = []
    created = updated = pages = 0
    if budget_seconds is not None:
        deadline = time.monotonic() + max(1.0, float(budget_seconds))
    elif fetch_page is not None:
        deadline = None
    else:
        deadline = time.monotonic() + INGEST_BUDGET_SECONDS
    for username in _usernames_by_priority():
        if deadline is not None and time.monotonic() >= deadline:
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
        )
        channels.append(result)
        created += int(result.get("created") or 0)
        updated += int(result.get("updated") or 0)
        pages += int(result.get("pages") or 0)
    return {
        "ok": True,
        "created": created,
        "updated": updated,
        "pages": pages,
        "channels": channels,
    }
