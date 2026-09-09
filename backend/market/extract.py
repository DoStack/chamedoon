from __future__ import annotations

import json
import logging
import traceback
from contextlib import contextmanager

from django.utils import timezone

from ai.openrouter import openrouter_enabled, openrouter_model
from market.ingest import (
    _cutoff,
    _upsert_post,
    fetch_preview_page,
    ingest_market_channel,
    market_channel_usernames,
)
from market.migrate import migrate_market_post
from market.models import MarketIngestState, MarketPost
from market.parse import parse_preview_html

logger = logging.getLogger(__name__)

CHANNEL_EXTRACT_LIMIT = 8


class ExtractLog:
    def __init__(self) -> None:
        self.lines: list[dict] = []

    def _add(self, level: str, message: str, detail: str = "") -> None:
        self.lines.append(
            {
                "level": level,
                "message": message,
                "detail": detail,
                "time": timezone.now().strftime("%H:%M:%S"),
            }
        )

    def info(self, message: str, detail: str = "") -> None:
        self._add("info", message, detail)

    def warn(self, message: str, detail: str = "") -> None:
        self._add("warn", message, detail)

    def error(self, message: str, detail: str = "") -> None:
        self._add("error", message, detail)

    def exception(self, message: str, exc: BaseException) -> None:
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self._add("error", f"{message}: {exc}", detail)
        logger.exception(message)


class _CaptureHandler(logging.Handler):
    def __init__(self, log: ExtractLog) -> None:
        super().__init__()
        self.log = log

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = f"{record.name}: {record.getMessage()}"
        except Exception:
            message = record.msg
        detail = ""
        if record.exc_info:
            detail = "".join(traceback.format_exception(*record.exc_info))
        if record.levelno >= logging.ERROR:
            self.log._add("error", message, detail)
        elif record.levelno >= logging.WARNING:
            self.log._add("warn", message, detail)
        else:
            self.log._add("info", message, detail)


@contextmanager
def _capture_logs(log: ExtractLog):
    handler = _CaptureHandler(log)
    attached = [logging.getLogger("market"), logging.getLogger("ai.openrouter")]
    for item in attached:
        item.addHandler(handler)
    try:
        yield
    finally:
        for item in attached:
            item.removeHandler(handler)


def extract_status() -> dict:
    names = market_channel_usernames()
    states = {
        row.channel_username: row
        for row in MarketIngestState.objects.filter(channel_username__in=names)
    }
    channels = []
    for name in names:
        state = states.get(name)
        channels.append(
            {
                "username": name,
                "last_run_at": state.last_run_at if state else None,
                "last_error": (state.last_error or "").strip() if state else "",
                "last_created": state.last_created if state else 0,
            }
        )
    return {
        "openrouter": openrouter_enabled(),
        "model": openrouter_model() if openrouter_enabled() else "disabled",
        "channels": channels,
        "channel_names": names,
    }


def extract_one_post(username: str, *, force_review: bool = True, fetch_page=None) -> dict:
    log = ExtractLog()
    username = (username or "").strip().lstrip("@")
    try:
        with _capture_logs(log):
            post = _fetch_latest_post(username, log=log, fetch_page=fetch_page)
            if post is None:
                return {"ok": False, "logs": log.lines, "result": "no_post"}
            outcome = _convert_post(post, log=log, force_review=force_review)
            return {"ok": True, "logs": log.lines, "result": outcome, "post_id": post.pk}
    except Exception as exc:
        log.exception("Extract 1 post failed", exc)
        return {"ok": False, "logs": log.lines, "result": "error"}


def extract_channel(
    username: str,
    *,
    force_review: bool = False,
    fetch_page=None,
    limit: int = CHANNEL_EXTRACT_LIMIT,
) -> dict:
    log = ExtractLog()
    username = (username or "").strip().lstrip("@")
    try:
        with _capture_logs(log):
            _log_environment(log)
            if not username:
                log.error("No channel selected.")
                return {"ok": False, "logs": log.lines, "result": "no_channel"}
            log.info(f"Ingesting latest page from @{username} (no backfill).")
            ingest = ingest_market_channel(
                username=username,
                fetch_page=fetch_page,
                head_pages=1,
                backfill_pages=0,
            )
            log.info(
                f"Ingest @{username}: created={ingest.get('created')} updated={ingest.get('updated')} "
                f"pages={ingest.get('pages')} skipped={ingest.get('skipped', False)}",
                json.dumps({key: ingest.get(key) for key in ("ok", "error", "newest_message_id", "post_count")}, default=str),
            )
            if ingest.get("error"):
                log.warn("Ingest reported an error.", str(ingest.get("error")))
            posts = list(
                MarketPost.objects.filter(channel_username=username).order_by("-posted_at", "-telegram_message_id")[:limit]
            )
            log.info(f"Converting {len(posts)} newest stored post(s) from @{username}.")
            counts = {"created": 0, "updated": 0, "skipped": 0, "expired": 0, "deferred": 0}
            for post in posts:
                outcome = _convert_post(post, log=log, force_review=force_review)
                counts[outcome] = counts.get(outcome, 0) + 1
            log.info("Channel extract finished.", json.dumps(counts))
            return {"ok": True, "logs": log.lines, "result": counts}
    except Exception as exc:
        log.exception("Channel extract failed", exc)
        return {"ok": False, "logs": log.lines, "result": "error"}


def _log_environment(log: ExtractLog) -> None:
    if openrouter_enabled():
        log.info(f"OpenRouter is on. Model: {openrouter_model()}")
    else:
        log.warn("OPENROUTER_API_KEY is empty. Conversion will use regex rules, not the LLM.")


def _fetch_latest_post(username: str, *, log: ExtractLog, fetch_page=None) -> MarketPost | None:
    _log_environment(log)
    if not username:
        log.error("No channel selected.")
        return None
    fetch = fetch_page or fetch_preview_page
    log.info(f"Fetching https://t.me/s/{username}")
    html = fetch(username, None)
    posts = parse_preview_html(html, default_username=username)
    if not posts:
        log.error("Telegram preview returned no posts. The channel may be private, renamed, or blocked.")
        return None
    newest = max(posts, key=lambda item: int(item["telegram_message_id"]))
    log.info(
        f"Latest preview post is {username}/{newest['telegram_message_id']}.",
        (newest.get("text") or "")[:800],
    )
    result = _upsert_post(newest, cutoff=_cutoff())
    if result is None:
        log.error("That post is older than the 30-day ingest window, so it was not stored.")
        return None
    post, created = result
    log.info(f"{'Stored new' if created else 'Updated'} MarketPost #{post.pk} ({post.role}).")
    return post


def _convert_post(post: MarketPost, *, log: ExtractLog, force_review: bool) -> str:
    log.info(
        f"LLM/convert MarketPost #{post.pk} @{post.channel_username}/{post.telegram_message_id} "
        f"force_review={force_review}"
    )
    outcome = migrate_market_post(post, force_review=force_review)
    post.refresh_from_db()
    review = post.review_json if isinstance(post.review_json, dict) else {}
    detail = {
        "outcome": outcome,
        "skip_reason": post.skip_reason,
        "role": post.role,
        "item_request_id": post.item_request_id,
        "reviewed_at": post.reviewed_at.isoformat() if post.reviewed_at else "",
        "review": {
            key: review.get(key)
            for key in (
                "accept",
                "reject_reason",
                "role",
                "origin_city",
                "origin_country",
                "destination_city",
                "destination_country",
                "error",
                "description",
            )
            if key in review
        },
    }
    if outcome in {"created", "updated"}:
        log.info(f"Converted to request #{post.item_request_id} ({outcome}).", json.dumps(detail, default=str))
    elif outcome == "deferred":
        log.warn("LLM budget deferred this post.", json.dumps(detail, default=str))
    else:
        log.warn(f"Did not convert ({outcome}). skip_reason={post.skip_reason or '—'}", json.dumps(detail, default=str))
    return outcome

