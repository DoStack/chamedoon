from __future__ import annotations

import json
import logging
import re
import time
import traceback
from contextlib import contextmanager

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from ai.llm import llm_enabled, llm_status
from item_requests.models import RequestType
from item_requests.seed import CATEGORIES, CITIES, COUNTRIES
from market.catalog import catalog_location
from market.classify import classify_role, extract_route
from market.ingest import (
    DEFAULT_EXTRACT_DAYS,
    _cutoff,
    _upsert_post,
    clamp_lookback_days,
    fetch_preview_around,
    fetch_preview_page,
    ingest_market_channel,
    market_channel_usernames,
    lookback_complete,
    oldest_posted_at,
    pending_post_count,
)
from item_requests.services import create_item_request, update_item_request
from market.dates import is_past_travel_date
from market.job import run_market_job
from market.migrate import (
    SKIP_EXPIRED,
    _clean_with_rules,
    _source_url,
    migrate_market_post,
    migrate_market_posts,
    owner_for_post,
)
from market.models import MarketIngestState, MarketPost, MarketRole
from market.parse import parse_preview_html
from market.review import (
    payload_from_review,
    resolve_listing_description,
    review_market_post,
)
from notifications.channel import (
    channel_chat_id,
    channel_enabled,
    publish_unpublished_imported,
    sync_request_channel,
    unpublished_imported_count,
)

_HANDLE = re.compile(r"@([A-Za-z0-9_]{3,32})")
_TELEGRAM_POST_URL = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/(?:s/)?(?P<user>[A-Za-z0-9_]{3,32})/(?P<id>\d+)",
    re.I,
)
_TELEGRAM_POST_REF = re.compile(r"^@?(?P<user>[A-Za-z0-9_]{3,32})/(?P<id>\d+)$")

logger = logging.getLogger(__name__)

CHANNEL_EXTRACT_LIMIT = 8
EXTRACT_JOB_SECONDS = 48
CONVERT_JOB_SECONDS = 40
CONVERT_BATCH = 40
PUBLISH_JOB_SECONDS = 40


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
    attached = [
        logging.getLogger("market"),
        logging.getLogger("ai.llm"),
        logging.getLogger("ai.openrouter"),
        logging.getLogger("ai.openai"),
        logging.getLogger("notifications"),
    ]
    for item in attached:
        item.addHandler(handler)
    try:
        yield
    finally:
        for item in attached:
            item.removeHandler(handler)


def extract_status(*, days: int | None = None) -> dict:
    names = market_channel_usernames()
    lookback = clamp_lookback_days(days, default=DEFAULT_EXTRACT_DAYS)
    cutoff = _cutoff(lookback)
    states = {
        row.channel_username: row
        for row in MarketIngestState.objects.filter(channel_username__in=names)
    }
    channels = []
    behind = 0
    for name in names:
        state = states.get(name)
        covered = lookback_complete(name, lookback)
        if not covered:
            behind += 1
        channels.append(
            {
                "username": name,
                "last_run_at": state.last_run_at if state else None,
                "last_error": (state.last_error or "").strip() if state else "",
                "last_created": state.last_created if state else 0,
                "extract_cursor_id": state.extract_cursor_id if state else None,
                "window_covered": covered,
                "oldest_posted_at": oldest_posted_at(name),
                "post_count": MarketPost.objects.filter(channel_username=name).count(),
            }
        )
    status = llm_status()
    return {
        "openrouter": status["openrouter"],
        "openai": status["openai"],
        "model": status["model"],
        "free_models": status["free_models"],
        "paid_model": status["paid_model"],
        "channel_publish": channel_enabled() and bool(channel_chat_id()),
        "channels": channels,
        "channel_names": names,
        "pending_posts": pending_post_count(cutoff=cutoff),
        "channels_behind": behind,
        "lookback_days": lookback,
        "unpublished_requests": unpublished_imported_count(),
    }


def parse_telegram_post_ref(value: str) -> tuple[str, int] | None:
    raw = (value or "").strip()
    if not raw:
        return None
    match = _TELEGRAM_POST_URL.search(raw) or _TELEGRAM_POST_REF.fullmatch(raw)
    if not match:
        return None
    return match.group("user"), int(match.group("id"))


def extract_one_post(username: str, *, force_review: bool = True, fetch_page=None) -> dict:
    log = ExtractLog()
    username = (username or "").strip().lstrip("@")
    try:
        with _capture_logs(log):
            post, fetch_reason = _fetch_next_post(username, log=log, fetch_page=fetch_page)
            if fetch_reason == "caught_up":
                return {"ok": True, "logs": log.lines, "result": "caught_up", "draft": None}
            if post is None:
                return {"ok": False, "logs": log.lines, "result": "no_post"}
            return _result_for_post(post, log=log, force_review=force_review)
    except Exception as exc:
        log.exception("Extract 1 post failed", exc)
        return {"ok": False, "logs": log.lines, "result": "error"}


def extract_named_post(
    username: str = "",
    message_id: int | str | None = None,
    *,
    url: str = "",
    force_review: bool = True,
    fetch_page=None,
) -> dict:
    log = ExtractLog()
    try:
        with _capture_logs(log):
            parsed = parse_telegram_post_ref(url) if url else None
            if parsed:
                username, message_id = parsed
            username = (username or "").strip().lstrip("@")
            try:
                message_id = int(message_id)
            except (TypeError, ValueError):
                log.error("Paste a Telegram post URL such as https://t.me/koolbar_international/7265.")
                return {"ok": False, "logs": log.lines, "result": "bad_url"}
            if not username:
                log.error("Channel username is missing.")
                return {"ok": False, "logs": log.lines, "result": "no_channel"}
            post = _fetch_named_post(username, message_id, log=log, fetch_page=fetch_page)
            if post is None:
                return {"ok": False, "logs": log.lines, "result": "not_found"}
            return _result_for_post(post, log=log, force_review=force_review)
    except Exception as exc:
        log.exception("Extract this post failed", exc)
        return {"ok": False, "logs": log.lines, "result": "error"}


def extract_all_channels(*, fetch_page=None, days: int | str | None = None) -> dict:
    log = ExtractLog()
    started = time.monotonic()
    deadline = None if fetch_page is not None else started + EXTRACT_JOB_SECONDS
    lookback = clamp_lookback_days(days, default=DEFAULT_EXTRACT_DAYS)
    try:
        with _capture_logs(log):
            names = market_channel_usernames()
            if not names:
                log.error("No MARKET_CHANNEL_USERNAMES configured.")
                return {"ok": False, "logs": log.lines, "result": "no_channel"}
            log.info(
                f"Crawling {len(names)} channel(s) for the last {lookback} day(s), then converting "
                f"real send/carry posts into Explore: " + ", ".join(f"@{name}" for name in names)
            )
            job = run_market_job(
                fetch_page=fetch_page,
                stop_at=deadline,
                days=lookback,
            )
            log.info(f"Expired {job.get('expired', 0)} past-dated request(s).")
            ingest = job.get("ingest") or {}
            behind = 0
            for item in ingest.get("channels") or []:
                channel = item.get("channel") or "?"
                if item.get("deferred"):
                    behind += 1
                    log.warn(f"@{channel}: deferred so this request can return before Vercel times out.")
                elif item.get("error"):
                    log.warn(f"@{channel}: {item.get('error')}")
                else:
                    covered = item.get("window_covered")
                    if covered is False:
                        behind += 1
                    log.info(
                        f"Ingest @{channel}: created={item.get('created')} updated={item.get('updated')} "
                        f"pages={item.get('pages')} covered={covered}"
                    )
            log.info(
                f"Ingest total: created={ingest.get('created')} updated={ingest.get('updated')} "
                f"pages={ingest.get('pages')}"
            )
            migrated = job.get("migrate") or {}
            log.info("Convert finished.", json.dumps(migrated, default=str))
            if ingest.get("truncated") or behind:
                log.warn(
                    f"{behind} channel(s) still have older posts in the {lookback}-day window. "
                    "Run extraction again to continue the crawl."
                )
            if migrated.get("truncated"):
                log.warn("Convert stopped after a batch. Run extraction or Convert stored posts again.")
            pending = pending_post_count(days=lookback)
            log.info(f"{pending} unconverted send/carry candidate(s) remain in the last {lookback} day(s).")
            published = job.get("publish") or {}
            if published.get("published") or published.get("failed") or published.get("remaining"):
                log.info("Channel publish finished.", json.dumps(published, default=str))
            return {
                "ok": bool(job.get("ok")),
                "logs": log.lines,
                "result": "job",
                "days": lookback,
                "expired": job.get("expired", 0),
                "ingest": ingest,
                "migrate": migrated,
                "publish": published,
            }
    except Exception as exc:
        log.exception("Run extraction failed", exc)
        return {"ok": False, "logs": log.lines, "result": "error"}


def convert_stored_posts(*, days: int | str | None = None) -> dict:
    log = ExtractLog()
    lookback = clamp_lookback_days(days, default=DEFAULT_EXTRACT_DAYS)
    stop_at = time.monotonic() + CONVERT_JOB_SECONDS
    try:
        with _capture_logs(log):
            pending = pending_post_count(days=lookback)
            log.info(
                f"Converting up to {CONVERT_BATCH} newest unconverted posts from the last "
                f"{lookback} day(s). {pending} candidates. Rules only — OpenAI is not called."
            )
            migrated = migrate_market_posts(
                stop_at=stop_at,
                wait_for_llm=False,
                pending_only=True,
                newest_first=True,
                max_posts=CONVERT_BATCH,
                posted_after=_cutoff(lookback),
                sync_channel=False,
            )
            if migrated.get("truncated"):
                log.warn("Stopped after a batch so Vercel does not 504. Convert stored posts again.")
            log.info("Convert finished.", json.dumps(migrated, default=str))
            still_pending = pending_post_count(days=lookback)
            log.info(f"{still_pending} candidate(s) remain in the last {lookback} day(s).")
            waiting = unpublished_imported_count()
            if waiting:
                log.info(f"{waiting} imported request(s) are not on the Koolbar channel yet. Use Publish to channel.")
            return {
                "ok": bool(migrated.get("ok")),
                "logs": log.lines,
                "result": "convert",
                "days": lookback,
                "expired": 0,
                "ingest": {"created": 0, "updated": 0, "truncated": False},
                "migrate": migrated,
                "publish": {"ok": True, "published": 0, "failed": 0, "truncated": False, "remaining": waiting},
            }
    except Exception as exc:
        log.exception("Convert stored posts failed", exc)
        return {"ok": False, "logs": log.lines, "result": "error"}


def publish_stored_requests() -> dict:
    log = ExtractLog()
    stop_at = time.monotonic() + PUBLISH_JOB_SECONDS
    try:
        with _capture_logs(log):
            waiting = unpublished_imported_count()
            if not channel_enabled() or not channel_chat_id():
                log.error("Koolbar channel is not configured (TELEGRAM_CHANNEL_ENABLED / TELEGRAM_CHANNEL_ID).")
                return {
                    "ok": False,
                    "logs": log.lines,
                    "result": "publish",
                    "publish": {"ok": False, "published": 0, "failed": 0, "remaining": waiting},
                }
            log.info(f"Publishing up to 10 unpublished imported requests. {waiting} waiting.")
            published = publish_unpublished_imported(stop_at=stop_at)
            if published.get("truncated"):
                log.warn("Stopped after a batch so Vercel does not 504. Publish to channel again.")
            log.info("Channel publish finished.", json.dumps(published, default=str))
            return {
                "ok": bool(published.get("ok")),
                "logs": log.lines,
                "result": "publish",
                "publish": published,
            }
    except Exception as exc:
        log.exception("Publish to channel failed", exc)
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


def reset_extract_cursor(username: str) -> dict:
    log = ExtractLog()
    username = (username or "").strip().lstrip("@")
    if not username:
        log.error("No channel selected.")
        return {"ok": False, "logs": log.lines, "result": "no_channel"}
    state = _channel_state(username)
    state.extract_cursor_id = None
    state.save(update_fields=["extract_cursor_id"])
    log.info(f"Cursor reset for @{username}. Next Extract 1 post will take the latest message.")
    return {"ok": True, "logs": log.lines, "result": "reset"}


def _log_environment(log: ExtractLog) -> None:
    status = llm_status()
    if status["enabled"]:
        log.info(f"LLMs: {status['model']}")
    else:
        log.warn("No LLM key is set. Fields will be filled from the post text.")


def _fetch_next_post(username: str, *, log: ExtractLog, fetch_page=None) -> tuple[MarketPost | None, str]:
    _log_environment(log)
    if not username:
        log.error("No channel selected.")
        return None, "no_channel"
    fetch = fetch_page or fetch_preview_page
    state = _channel_state(username)
    cursor = state.extract_cursor_id
    if cursor:
        log.info(f"Walking older than @{username}/{cursor}.")
    else:
        log.info(f"Starting from the latest preview post on @{username}.")
    log.info(f"Fetching https://t.me/s/{username}")
    chosen = _pick_next_preview_post(fetch(username, None), username, cursor)
    if chosen is None and cursor:
        log.info(f"Fetching https://t.me/s/{username}?before={cursor}")
        chosen = _pick_next_preview_post(fetch(username, cursor), username, cursor)
    if chosen is None:
        if cursor:
            log.warn(
                f"No older posts left in the 30-day window after @{username}/{cursor}. "
                "Reset to latest to start from the newest message again."
            )
            return None, "caught_up"
        log.error("Telegram preview returned no posts. The channel may be private, renamed, or blocked.")
        return None, "no_post"
    log.info(
        f"Next preview post is {username}/{chosen['telegram_message_id']}.",
        (chosen.get("text") or "")[:800],
    )
    result = _upsert_post(chosen, cutoff=_cutoff())
    if result is None:
        log.error("That post is older than the 30-day ingest window, so it was not stored.")
        return None, "caught_up"
    post, created = result
    state.extract_cursor_id = post.telegram_message_id
    state.save(update_fields=["extract_cursor_id"])
    log.info(
        f"{'Stored new' if created else 'Updated'} MarketPost #{post.pk} "
        f"({post.role}) @{post.channel_username}/{post.telegram_message_id}."
    )
    return post, "ok"


def _fetch_named_post(username: str, message_id: int, *, log: ExtractLog, fetch_page=None) -> MarketPost | None:
    _log_environment(log)
    log.info(f"Fetching https://t.me/s/{username}/{message_id}")
    fetch = fetch_page or (lambda _name, _before: fetch_preview_around(username, message_id))
    html = fetch(username, message_id)
    posts = parse_preview_html(html, default_username=username)
    if not posts:
        log.error("Telegram preview returned no posts for that URL.")
        return None
    stored = 0
    cutoff = _cutoff()
    target_raw = None
    for raw in posts:
        is_target = int(raw["telegram_message_id"]) == int(message_id)
        if is_target:
            target_raw = raw
        result = _upsert_post(raw, cutoff=cutoff, ignore_cutoff=is_target)
        if result is not None:
            stored += 1
    log.info(f"Stored {stored} post(s) from that preview page.")
    if target_raw is None:
        log.error(f"@{username}/{message_id} was not on the public preview page.")
        return None
    result = _upsert_post(target_raw, cutoff=cutoff, ignore_cutoff=True)
    if result is None:
        log.error(f"Could not store @{username}/{message_id}.")
        return None
    post, created = result
    log.info(
        f"{'Stored new' if created else 'Updated'} MarketPost #{post.pk} "
        f"({post.role}) @{post.channel_username}/{post.telegram_message_id}."
    )
    return post


def _result_for_post(post: MarketPost, *, log: ExtractLog, force_review: bool) -> dict:
    if not (post.text or "").strip():
        log.warn("Skipped: Telegram preview has no text (photo/video only).")
        return {
            "ok": True,
            "logs": log.lines,
            "result": "no_text",
            "post_id": post.pk,
            "draft": None,
            "preview": _preview_from_post(post),
        }
    if _is_noise(post):
        log.warn("Skipped: this is a promo, group invite, or ad — not a send/carry request.")
        return {
            "ok": True,
            "logs": log.lines,
            "result": "noise",
            "post_id": post.pk,
            "draft": None,
            "preview": _preview_from_post(post),
        }
    if is_past_travel_date(post.text, posted_at=post.posted_at, today=timezone.now().date()):
        post.skip_reason = SKIP_EXPIRED
        post.save(update_fields=["skip_reason", "updated_at"])
        log.warn("Skipped: the flight date is already past, so this is not a valid request.")
        return {
            "ok": True,
            "logs": log.lines,
            "result": "expired",
            "post_id": post.pk,
            "draft": None,
            "preview": _preview_from_post(post),
        }
    draft = _review_draft(post, log=log, force_review=force_review)
    log.info("Review the draft below, then convert it to a request.")
    return {
        "ok": True,
        "logs": log.lines,
        "result": "draft",
        "post_id": post.pk,
        "draft": draft,
    }


def _pick_next_preview_post(html: str, username: str, cursor: int | None) -> dict | None:
    posts = parse_preview_html(html, default_username=username)
    cutoff = _cutoff()
    fresh = [item for item in posts if item.get("posted_at") and item["posted_at"] >= cutoff]
    if cursor is not None:
        fresh = [item for item in fresh if int(item["telegram_message_id"]) < int(cursor)]
    if not fresh:
        return None
    return max(fresh, key=lambda item: int(item["telegram_message_id"]))


def _channel_state(username: str) -> MarketIngestState:
    state, _created = MarketIngestState.objects.get_or_create(channel_username=username)
    return state


def _convert_post(post: MarketPost, *, log: ExtractLog, force_review: bool) -> str:
    log.info(
        f"LLM/convert MarketPost #{post.pk} @{post.channel_username}/{post.telegram_message_id} "
        f"force_review={force_review}"
    )
    outcome = migrate_market_post(post, force_review=force_review, wait_for_llm=False)
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


def extract_catalog() -> dict:
    from item_requests.models import City
    from item_requests.weights import category_kg_map

    cities = list(CITIES)
    known = {(item["country"], item["slug"]) for item in cities}
    for city in City.objects.filter(is_active=True).select_related("country"):
        key = (city.country.code, city.slug)
        if key in known:
            continue
        cities.append(
            {
                "country": city.country.code,
                "slug": city.slug,
                "name_en": city.name_en,
                "name_fa": city.name_fa,
            }
        )
        known.add(key)
    return {
        "countries": COUNTRIES,
        "cities": cities,
        "categories": CATEGORIES,
        "category_kg": category_kg_map(),
    }


def _is_noise(post: MarketPost) -> bool:
    return post.role == MarketRole.NOISE or classify_role(post.text) == MarketRole.NOISE


def _preview_from_post(post: MarketPost) -> dict:
    return {
        "post_id": post.pk,
        "channel": post.channel_username,
        "message_id": post.telegram_message_id,
        "source_url": _source_url(post),
        "text": post.text,
        "role": post.role or classify_role(post.text),
    }


def convert_reviewed_post(post_id: int | str, data) -> dict:
    log = ExtractLog()
    try:
        pk = int(post_id)
    except (TypeError, ValueError):
        log.error("Missing market post. Extract 1 post first.")
        return {"ok": False, "logs": log.lines, "result": "error", "draft": _draft_from_form(None, data)}
    post = MarketPost.objects.filter(pk=pk).first()
    if post is None:
        log.error(f"MarketPost #{pk} was not found.")
        return {"ok": False, "logs": log.lines, "result": "error", "draft": _draft_from_form(None, data)}
    payload = payload_from_form(data)
    author = (_form_value(data, "author_username") or "").strip().lstrip("@")
    draft = _draft_from_form(post, data)
    if is_past_travel_date(post.text, posted_at=post.posted_at, today=timezone.now().date()):
        post.skip_reason = SKIP_EXPIRED
        post.save(update_fields=["skip_reason", "updated_at"])
        log.error("The flight date in this post is already past. It cannot be converted to a request.")
        draft["errors"] = {"flight_date": "This flight date is already past."}
        return {
            "ok": False,
            "logs": log.lines,
            "result": "expired",
            "post_id": post.pk,
            "draft": draft,
        }
    try:
        with _capture_logs(log):
            owner = owner_for_post(post, author)
            if post.item_request_id:
                item = post.item_request
                update_payload = {key: value for key, value in payload.items() if key != "type"}
                if item.type != payload["type"]:
                    log.error("This post is already a request. Type cannot be changed; update the other fields or expire it first.")
                    draft["errors"] = {"type": "Type cannot be changed on an existing request."}
                    return {"ok": False, "logs": log.lines, "result": "error", "draft": draft, "post_id": post.pk}
                update_item_request(item, update_payload)
                if item.user_id != owner.id:
                    item.user = owner
                    item.save(update_fields=["user", "updated_at"])
                outcome = "updated"
            else:
                item = create_item_request(owner, payload, imported=True, source_url=_source_url(post))
                post.item_request = item
                outcome = "created"
            post.skip_reason = ""
            post.role = MarketRole.SUPPLY if payload["type"] == RequestType.SUPPLY else MarketRole.DEMAND
            post.migrated_at = timezone.now()
            if author:
                post.author_username = author[:64]
            post.save(update_fields=["item_request", "skip_reason", "role", "migrated_at", "author_username", "updated_at"])
            item.refresh_from_db()
            log.info(f"Converted MarketPost #{post.pk} to request #{item.pk} ({outcome}).")
            published = _publish_converted(item, log)
            draft = _draft_from_item(post, item, payload, author)
            draft["published"] = published
            return {
                "ok": True,
                "logs": log.lines,
                "result": outcome,
                "post_id": post.pk,
                "item_request_id": item.pk,
                "draft": draft,
            }
    except ValidationError as exc:
        log.error("That draft is not a valid request.", _validation_detail(exc))
        draft["errors"] = _validation_messages(exc)
        return {"ok": False, "logs": log.lines, "result": "invalid", "post_id": post.pk, "draft": draft}
    except Exception as exc:
        log.exception("Convert to request failed", exc)
        return {"ok": False, "logs": log.lines, "result": "error", "post_id": post.pk, "draft": draft}


def payload_from_form(data) -> dict:
    request_type = (_form_value(data, "type") or RequestType.DEMAND).strip().upper()
    if request_type not in {RequestType.DEMAND, RequestType.SUPPLY}:
        request_type = RequestType.DEMAND
    kg = (_form_value(data, "weight_kg") or _form_value(data, "capacity_kg") or "").strip()
    payload: dict = {
        "type": request_type,
        "origin_country": (_form_value(data, "origin_country") or "").strip().upper(),
        "origin_city": (_form_value(data, "origin_city") or "").strip(),
        "destination_country": "",
        "destination_city": "",
        "destination_cities": _destinations_from_form(data),
        "description": (_form_value(data, "description") or "").strip(),
        "item_category_codes": _form_values(data, "item_category_codes"),
    }
    if request_type == RequestType.SUPPLY:
        payload["capacity_kg"] = kg
        flight = (_form_value(data, "flight_date") or _form_value(data, "desired_date") or "").strip()
        payload["flight_date"] = flight
        payload["date_from"] = (_form_value(data, "date_from") or flight).strip()
        payload["date_to"] = (_form_value(data, "date_to") or payload["date_from"]).strip()
        payload["excluded_category_codes"] = _form_values(data, "excluded_category_codes")
        payload["excluded_other_text"] = (_form_value(data, "excluded_other_text") or "").strip()
    else:
        payload["weight_kg"] = kg
        payload["desired_date"] = (_form_value(data, "desired_date") or _form_value(data, "flight_date") or "").strip()
    return payload


def _review_draft(post: MarketPost, *, log: ExtractLog, force_review: bool) -> dict:
    _apply_text_author(post)
    llm_error = ""
    payload = None
    if llm_enabled():
        log.info(
            f"LLM/review MarketPost #{post.pk} @{post.channel_username}/{post.telegram_message_id} "
            f"force_review={force_review}"
        )
        review = review_market_post(post, force=force_review)
        post.refresh_from_db()
        if review.error:
            llm_error = review.error
            log.warn(
                f"All LLMs returned no usable data ({review.error}). "
                "The draft below is filled from the post text so you can still convert it."
            )
        elif review.reject_reason == SKIP_EXPIRED:
            log.warn("Skipped: the flight date is already past, so this is not a valid request.")
            return _draft_from_payload(post, None, llm_error=llm_error)
        else:
            payload, reason = payload_from_review(post, review, draft=True)
            if payload:
                used = review.model or "LLM"
                dests = ", ".join(
                    f"{item['city']}" for item in payload.get("destination_cities") or []
                ) or payload.get("destination_city")
                log.info(
                    f"{used} filled the draft: {payload['type']} "
                    f"{payload['origin_city']} → {dests}."
                )
            elif reason == SKIP_EXPIRED:
                log.warn("Skipped: the flight date is already past, so this is not a valid request.")
                return _draft_from_payload(post, None, llm_error=llm_error)
            elif not review.error:
                log.warn(
                    f"LLM did not produce a listing ({reason or 'rejected'}). "
                    "The draft below is filled from the post text."
                )
    else:
        log.warn("No LLM key is set. Draft filled from the post text.")

    if payload is None:
        rules, reason = _rules_payload(post)
        if reason == SKIP_EXPIRED:
            log.warn("Skipped: the flight date is already past, so this is not a valid request.")
            return _draft_from_payload(post, None, llm_error=llm_error)
        if rules:
            payload = rules
            log.info(
                f"Draft from post text: {payload['type']} "
                f"{payload['origin_city']} → {payload['destination_city']}."
            )
        else:
            log.warn("Could not auto-fill route or dates. Complete the form manually, then convert.")
    return _draft_from_payload(post, payload, llm_error=llm_error)


def _rules_payload(post: MarketPost) -> tuple[dict | None, str | None]:
    guessed = classify_role(post.text)
    if guessed == MarketRole.NOISE:
        if post.role != MarketRole.NOISE:
            post.role = MarketRole.NOISE
            post.save(update_fields=["role", "updated_at"])
        return None, "noise"
    if guessed in {MarketRole.SUPPLY, MarketRole.DEMAND} and post.role != guessed:
        post.role = guessed
        post.save(update_fields=["role", "updated_at"])
    if post.role not in {MarketRole.SUPPLY, MarketRole.DEMAND}:
        return None, "unknown_role"
    return _clean_with_rules(post, date_fallback=True)


def _draft_from_payload(post: MarketPost, payload: dict | None, *, llm_error: str = "") -> dict:
    origin_country, origin_city = _location_from_post(post, "origin")
    dest_country, dest_city = _location_from_post(post, "destination")
    request_type = RequestType.DEMAND
    kg = ""
    desired = timezone.now().date().isoformat()
    if payload:
        request_type = payload.get("type") or request_type
        origin_country = payload.get("origin_country") or origin_country
        origin_city = payload.get("origin_city") or origin_city
        dest_country = payload.get("destination_country") or dest_country
        dest_city = payload.get("destination_city") or dest_city
        dest_keys = _destination_keys(payload)
        kg = str(payload.get("weight_kg") or payload.get("capacity_kg") or "")
        desired = payload.get("desired_date") or payload.get("flight_date") or desired
    else:
        dest_keys = _destination_keys(
            {"destination_country": dest_country, "destination_city": dest_city}
        )
    author = listing_author_username(post)
    return {
        "post_id": post.pk,
        "channel": post.channel_username,
        "message_id": post.telegram_message_id,
        "source_url": _source_url(post),
        "text": post.text,
        "role": post.role,
        "llm_error": llm_error,
        "type": request_type,
        "origin_country": origin_country,
        "origin_city": origin_city,
        "destination_country": dest_country,
        "destination_city": dest_city,
        "destination_keys": dest_keys,
        "desired_date": (payload or {}).get("desired_date") or desired,
        "flight_date": (payload or {}).get("flight_date") or "",
        "date_from": (payload or {}).get("date_from") or "",
        "date_to": (payload or {}).get("date_to") or "",
        "weight_kg": kg,
        "item_category_codes": list((payload or {}).get("item_category_codes") or []),
        "excluded_category_codes": list((payload or {}).get("excluded_category_codes") or []),
        "description": _draft_description(post, payload, origin_city=origin_city, dest_city=dest_city),
        "author_username": author,
        "contact_url": f"https://t.me/{author}" if author else "",
        "item_request_id": post.item_request_id,
        "errors": {},
    }


def _draft_from_form(post: MarketPost | None, data) -> dict:
    payload = payload_from_form(data)
    return {
        "post_id": int(_form_value(data, "post_id") or 0) or (post.pk if post else None),
        "channel": (post.channel_username if post else _form_value(data, "channel")) or "",
        "message_id": post.telegram_message_id if post else "",
        "source_url": _source_url(post) if post else "",
        "text": post.text if post else "",
        "role": post.role if post else "",
        "llm_error": _form_value(data, "llm_error") or "",
        "type": payload["type"],
        "origin_country": payload.get("origin_country") or "",
        "origin_city": payload.get("origin_city") or "",
        "destination_country": payload.get("destination_country") or "",
        "destination_city": payload.get("destination_city") or "",
        "destination_keys": _destination_keys(payload),
        "desired_date": payload.get("desired_date") or "",
        "flight_date": payload.get("flight_date") or "",
        "date_from": payload.get("date_from") or "",
        "date_to": payload.get("date_to") or "",
        "weight_kg": payload.get("weight_kg") or payload.get("capacity_kg") or "",
        "item_category_codes": payload.get("item_category_codes") or [],
        "excluded_category_codes": payload.get("excluded_category_codes") or [],
        "description": payload.get("description") or "",
        "author_username": (_form_value(data, "author_username") or "").strip().lstrip("@"),
        "contact_url": (
            f"https://t.me/{(_form_value(data, 'author_username') or '').strip().lstrip('@')}"
            if (_form_value(data, "author_username") or "").strip()
            else ""
        ),
        "item_request_id": post.item_request_id if post else None,
        "errors": {},
    }


def _draft_from_item(post: MarketPost, item, payload: dict, author: str) -> dict:
    draft = _draft_from_payload(post, payload)
    draft["item_request_id"] = item.pk
    draft["author_username"] = author or draft["author_username"]
    if draft["author_username"]:
        draft["contact_url"] = f"https://t.me/{draft['author_username']}"
    return draft


def _draft_description(post: MarketPost, payload: dict | None, *, origin_city: str, dest_city: str) -> str:
    payload = payload or {}
    is_supply = payload.get("type") == RequestType.SUPPLY
    dests = [item.get("city") for item in (payload.get("destination_cities") or []) if item.get("city")]
    dest_pairs = [
        (str(item.get("country") or ""), str(item.get("city") or ""))
        for item in (payload.get("destination_cities") or [])
        if item.get("country") and item.get("city")
    ]
    return resolve_listing_description(
        post,
        str(payload.get("description") or ""),
        is_supply=is_supply,
        origin=payload.get("origin_city") or origin_city,
        dest=payload.get("destination_city") or dest_city,
        dests=dests,
        dest_pairs=dest_pairs,
        category_codes=list(payload.get("item_category_codes") or []),
    )


def _location_from_post(post: MarketPost, side: str) -> tuple[str, str]:
    country = getattr(post, f"{side}_country", "") or ""
    city = getattr(post, f"{side}_city", "") or ""
    mapped = catalog_location({"city": city, "country": country}) if country else None
    if mapped:
        return mapped
    origin, dest = extract_route(post.text)
    place = origin if side == "origin" else dest
    mapped = catalog_location(place)
    if mapped:
        return mapped
    return country, city


def listing_author_username(post: MarketPost) -> str:
    stored = (post.author_username or "").strip().lstrip("@")
    handle = _author_handle(post.text, post.channel_username)
    for candidate in (stored, handle):
        if candidate and not _is_source_channel(candidate, post.channel_username):
            return candidate[:64]
    return official_koolbar_author()


def official_koolbar_author() -> str:
    channel = (getattr(settings, "TELEGRAM_CHANNEL_USERNAME", "") or "").strip().lstrip("@")
    if channel:
        return channel[:64]
    return "koolbar"


def _is_source_channel(handle: str, channel: str = "") -> bool:
    needle = (handle or "").strip().lstrip("@").lower()
    if not needle:
        return False
    sources = {name.lower() for name in market_channel_usernames()}
    source = (channel or "").strip().lstrip("@").lower()
    if source:
        sources.add(source)
    return needle in sources


def _apply_text_author(post: MarketPost) -> str:
    handle = _author_handle(post.text, post.channel_username)
    if handle and not _is_source_channel(handle, post.channel_username):
        if not post.author_username or _is_source_channel(post.author_username, post.channel_username):
            post.author_username = handle[:64]
            post.save(update_fields=["author_username", "updated_at"])
    return listing_author_username(post)


def _author_handle(text: str, channel: str = "") -> str:
    channel_l = (channel or "").strip().lstrip("@").lower()
    for match in _HANDLE.finditer(text or ""):
        handle = match.group(1)
        if handle.lower() != channel_l and not _is_source_channel(handle, channel):
            return handle
    return ""


def _publish_converted(item, log: ExtractLog) -> bool:
    if not channel_enabled() or not channel_chat_id():
        log.warn(
            "Request saved, but the Koolbar channel is not configured "
            "(TELEGRAM_CHANNEL_ENABLED / TELEGRAM_CHANNEL_ID)."
        )
        return False
    published = sync_request_channel(item.pk)
    item.refresh_from_db()
    if published:
        log.info(f"Published to the Koolbar channel (message {item.channel_message_id}).")
        return True
    log.warn("Channel publish failed. Check that the bot is an admin of the channel.")
    return False


def _destinations_from_form(data) -> list[dict[str, str]]:
    stops: list[dict[str, str]] = []
    for raw in _form_values(data, "destinations") or _form_values(data, "destination_keys"):
        if ":" not in raw:
            continue
        country, city = raw.split(":", 1)
        country, city = country.strip().upper(), city.strip()
        stop = {"country": country, "city": city}
        if country and city and stop not in stops:
            stops.append(stop)
    if stops:
        return stops
    country = (_form_value(data, "destination_country") or "").strip().upper()
    city = (_form_value(data, "destination_city") or "").strip()
    if country and city:
        return [{"country": country, "city": city}]
    return []


def _destination_keys(payload: dict | None) -> list[str]:
    payload = payload or {}
    keys: list[str] = []
    for item in payload.get("destination_cities") or []:
        if not isinstance(item, dict):
            continue
        country = str(item.get("country") or "").upper().strip()
        city = str(item.get("city") or "").strip()
        key = f"{country}:{city}"
        if country and city and key not in keys:
            keys.append(key)
    if not keys:
        country = str(payload.get("destination_country") or "").upper().strip()
        city = str(payload.get("destination_city") or "").strip()
        if country and city:
            keys.append(f"{country}:{city}")
    return keys


def _form_value(data, key: str) -> str:
    value = data.get(key) if data is not None else None
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else ""
    return str(value)


def _form_values(data, key: str) -> list[str]:
    if data is None:
        return []
    if hasattr(data, "getlist"):
        return [str(item) for item in data.getlist(key) if str(item).strip()]
    raw = data.get(key) or []
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    return [str(item) for item in raw if str(item).strip()]


def _validation_messages(exc: ValidationError) -> dict:
    if hasattr(exc, "message_dict"):
        return {key: "; ".join(str(item) for item in values) for key, values in exc.message_dict.items()}
    return {"__all__": "; ".join(str(item) for item in getattr(exc, "messages", [exc]))}


def _validation_detail(exc: ValidationError) -> str:
    return json.dumps(_validation_messages(exc), ensure_ascii=False)

