"""Read housing sources with the member Telegram account and store each message once.

Reuses the market HistoryReader (Telethon, one connection per run). A run reads
new messages first, newest to oldest, until it meets the newest id it already
holds. Then it walks the backlog down to the lookback cutoff, so a first run
fills 30 days across several calls.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone as dt_timezone

from django.utils import timezone

from home.models import HomeMedia, HomePost, HomeSource
from home.review import text_key
from home.telegram import HomeHistoryReader
from market.telegram_history import history_configured

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 30
MAX_PAGES_PER_SOURCE = 80
TIMEOUT_HINT = "Telegram did not answer in time (it may be limiting the rate); the next run continues from here"
STOP_RESERVE_SECONDS = 2.0


def _should_stop(stop_at: float | None) -> bool:
    return stop_at is not None and time.monotonic() + STOP_RESERVE_SECONDS >= stop_at


def ingest_sources(
    *,
    days: int = DEFAULT_LOOKBACK_DAYS,
    stop_at: float | None = None,
    usernames: list[str] | None = None,
    reader: HistoryReader | None = None,
) -> dict:
    """Ingest every enabled source. Returns counts per source and overall."""
    if reader is None and not history_configured():
        from api.config_check import report_missing, telegram_reader_missing

        message = report_missing("housing ingest", telegram_reader_missing())
        return {"ok": False, "skipped": message, "error": message, "sources": {}, "created": 0}
    sources = HomeSource.objects.filter(enabled=True)
    if usernames:
        sources = sources.filter(username__in=usernames)
    ordered = sorted(sources, key=lambda s: s.last_run_at or datetime(1970, 1, 1, tzinfo=dt_timezone.utc))
    own_reader = reader is None
    reader = reader or HomeHistoryReader()
    result: dict[str, dict] = {}
    created_total = 0
    try:
        if own_reader:
            reader.open()
        for source in ordered:
            if _should_stop(stop_at):
                result[source.username] = {"skipped": "time budget"}
                continue
            outcome = ingest_source(reader, source, days=days, stop_at=stop_at)
            result[source.username] = outcome
            created_total += outcome.get("created", 0)
    except Exception as exc:  # noqa: BLE001 - report, never crash the cron
        logger.exception("Home ingest failed")
        return {"ok": False, "error": str(exc)[:300], "sources": result, "created": created_total}
    finally:
        if own_reader:
            reader.close()
    return {"ok": True, "sources": result, "created": created_total}


def ingest_source(reader, source: HomeSource, *, days: int, stop_at: float | None) -> dict:
    cutoff = timezone.now() - timedelta(days=days)
    pages = 0
    created = 0

    def walk(before: int | None, floor_id: int | None) -> tuple[int, bool, bool, int | None]:
        """Read pages downward. Returns (created, reached_boundary, failed, highest_id)."""
        nonlocal pages
        added = 0
        cursor = before
        highest = None
        while pages < MAX_PAGES_PER_SOURCE and not _should_stop(stop_at):
            page = reader.page(source.username, cursor)
            pages += 1
            if page is None:
                return added, False, True, highest
            if not page:
                return added, True, False, highest
            lowest = None
            for raw in page:
                message_id = raw["telegram_message_id"]
                highest = message_id if highest is None else max(highest, message_id)
                lowest = message_id if lowest is None else min(lowest, message_id)
                if raw["posted_at"] < cutoff or (floor_id is not None and message_id <= floor_id):
                    _lower(source, message_id)
                    return added, True, False, highest
                if _store(source, raw):
                    added += 1
                _lower(source, message_id)
            if lowest is None or lowest == cursor:
                return added, True, False, highest
            cursor = lowest
        return added, False, False, highest

    first_fill = source.newest_message_id is None
    added, reached, failed, highest = walk(None, source.newest_message_id)
    created += added
    # On a first fill everything from the top down to oldest_message_id was read without gaps,
    # so the top can be remembered even if the walk stopped early; later runs continue below it.
    # After that, the top only moves once the new messages above it were all read.
    if highest is not None and ((reached and not failed) or first_fill):
        if source.newest_message_id is None or highest > source.newest_message_id:
            source.newest_message_id = highest
    if not failed and not source.backfill_complete and (reached or first_fill):
        if source.oldest_message_id is None:
            source.backfill_complete = True
        else:
            more, done, failed, _top = walk(source.oldest_message_id, None)
            created += more
            if done and not failed:
                source.backfill_complete = True
    error = ""
    if failed:
        error = reader.error_for(source.username) or TIMEOUT_HINT
    link_media(source)
    source.last_run_at = timezone.now()
    source.last_error = error[:500]
    source.save(
        update_fields=["newest_message_id", "oldest_message_id", "backfill_complete", "last_run_at", "last_error"]
    )
    return {"created": created, "pages": pages, "error": error, "backfill_complete": source.backfill_complete}


def _lower(source: HomeSource, message_id: int) -> None:
    if source.oldest_message_id is None or message_id < source.oldest_message_id:
        source.oldest_message_id = message_id


def _store(source: HomeSource, raw: dict) -> bool:
    """Store one message. A photo becomes a media row, any text becomes a post. True when a post is new."""
    if raw.get("has_photo") and source.fetch_photos:
        HomeMedia.objects.get_or_create(
            source=source,
            telegram_message_id=raw["telegram_message_id"],
            defaults={"grouped_id": raw.get("grouped_id")},
        )
    text = (raw.get("text") or "").strip()
    if not text:
        return False
    _, created = HomePost.objects.get_or_create(
        source=source,
        telegram_message_id=raw["telegram_message_id"],
        defaults={
            "posted_at": raw["posted_at"],
            "text": text,
            "author_username": raw.get("author_username", ""),
            "author_name": raw.get("author_name", ""),
            "author_id": raw.get("author_id"),
            "grouped_id": raw.get("grouped_id"),
            "source_url": raw.get("source_url", ""),
            "views": raw.get("views"),
            "has_photo": bool(raw.get("has_photo")),
            "text_key": text_key(text),
        },
    )
    return created


def link_media(source: HomeSource) -> int:
    """Attach photos to their post: an album's captioned message, or the message itself.

    Parts of an album arrive as separate messages and the caption may land on a later page,
    so linking runs after the pages are read. Photos with no text anywhere stay unlinked.
    """
    linked = 0
    touched: set[int] = set()
    for media in HomeMedia.objects.filter(source=source, post__isnull=True):
        post = None
        if media.grouped_id:
            post = HomePost.objects.filter(source=source, grouped_id=media.grouped_id).order_by("telegram_message_id").first()
        if post is None:
            post = HomePost.objects.filter(source=source, telegram_message_id=media.telegram_message_id).first()
        if post is None:
            continue
        media.post = post
        media.save(update_fields=["post"])
        touched.add(post.pk)
        linked += 1
    for post in HomePost.objects.filter(pk__in=touched):
        rows = list(post.media.order_by("telegram_message_id"))
        for position, media in enumerate(rows):
            if media.position != position:
                media.position = position
                media.save(update_fields=["position"])
        post.has_photo = True
        post.photo_count = len(rows)
        post.save(update_fields=["has_photo", "photo_count"])
    return linked
