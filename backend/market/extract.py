from __future__ import annotations

import json
import logging
import re
import traceback
from contextlib import contextmanager

from django.core.exceptions import ValidationError
from django.utils import timezone

from ai.openrouter import openrouter_enabled, openrouter_model
from item_requests.models import RequestType
from item_requests.seed import CATEGORIES, CITIES, COUNTRIES
from market.catalog import catalog_location
from market.classify import classify_role, extract_route
from market.ingest import (
    _cutoff,
    _upsert_post,
    fetch_preview_page,
    ingest_market_channel,
    market_channel_usernames,
)
from item_requests.services import create_item_request, update_item_request
from market.migrate import _clean_with_rules, _source_url, migrate_market_post, owner_for_post
from market.models import MarketIngestState, MarketPost, MarketRole
from market.parse import parse_preview_html
from market.review import payload_from_review, resolve_listing_description, review_market_post
from notifications.channel import channel_chat_id, channel_enabled, sync_request_channel

_HANDLE = re.compile(r"@([A-Za-z0-9_]{3,32})")

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
    attached = [
        logging.getLogger("market"),
        logging.getLogger("ai.openrouter"),
        logging.getLogger("notifications"),
    ]
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
                "extract_cursor_id": state.extract_cursor_id if state else None,
            }
        )
    return {
        "openrouter": openrouter_enabled(),
        "model": openrouter_model() if openrouter_enabled() else "disabled",
        "channel_publish": channel_enabled() and bool(channel_chat_id()),
        "channels": channels,
        "channel_names": names,
    }


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
            if _is_noise(post):
                log.warn(
                    "Skipped: this is a promo, group invite, or ad — not a send/carry request."
                )
                return {
                    "ok": True,
                    "logs": log.lines,
                    "result": "noise",
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
    if openrouter_enabled():
        log.info(f"OpenRouter is on. Model: {openrouter_model()}")
    else:
        log.warn("OPENROUTER_API_KEY is empty. Fields will be filled from the post text, not the LLM.")


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


def extract_catalog() -> dict:
    return {
        "countries": COUNTRIES,
        "cities": CITIES,
        "categories": CATEGORIES,
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
        "destination_country": (_form_value(data, "destination_country") or "").strip().upper(),
        "destination_city": (_form_value(data, "destination_city") or "").strip(),
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
    if openrouter_enabled():
        log.info(
            f"LLM/review MarketPost #{post.pk} @{post.channel_username}/{post.telegram_message_id} "
            f"force_review={force_review}"
        )
        review = review_market_post(post, force=force_review)
        post.refresh_from_db()
        if review.error:
            llm_error = review.error
            log.warn(
                f"OpenRouter returned no usable data ({review.error}). "
                "The draft below is filled from the post text so you can still convert it."
            )
        payload, reason = payload_from_review(post, review)
        if payload:
            log.info("LLM accepted this post. Check the fields below, then convert to a request.")
        elif not review.error:
            log.warn(
                f"LLM did not produce a listing ({reason or 'rejected'}). "
                "The draft below is filled from the post text."
            )
    else:
        log.warn("OPENROUTER_API_KEY is empty. Draft filled from the post text.")

    if payload is None:
        payload, _reason = _rules_payload(post)
        if payload:
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
        origin, dest = extract_route(post.text)
        if origin and dest:
            post.role = MarketRole.DEMAND
            post.save(update_fields=["role", "updated_at"])
        else:
            return None, "unknown_role"
    return _clean_with_rules(post)


def _draft_from_payload(post: MarketPost, payload: dict | None, *, llm_error: str = "") -> dict:
    origin_country, origin_city = _location_from_post(post, "origin")
    dest_country, dest_city = _location_from_post(post, "destination")
    request_type = RequestType.DEMAND
    kg = "10.00"
    desired = timezone.now().date().isoformat()
    if payload:
        request_type = payload.get("type") or request_type
        origin_country = payload.get("origin_country") or origin_country
        origin_city = payload.get("origin_city") or origin_city
        dest_country = payload.get("destination_country") or dest_country
        dest_city = payload.get("destination_city") or dest_city
        kg = str(payload.get("weight_kg") or payload.get("capacity_kg") or kg)
        desired = payload.get("desired_date") or payload.get("flight_date") or desired
    author = post.author_username or _author_handle(post.text, post.channel_username)
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
        "desired_date": (payload or {}).get("desired_date") or desired,
        "flight_date": (payload or {}).get("flight_date") or "",
        "date_from": (payload or {}).get("date_from") or "",
        "date_to": (payload or {}).get("date_to") or "",
        "weight_kg": kg,
        "item_category_codes": list((payload or {}).get("item_category_codes") or []),
        "excluded_category_codes": list((payload or {}).get("excluded_category_codes") or []),
        "description": _draft_description(post, payload, origin_city=origin_city, dest_city=dest_city),
        "author_username": author,
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
        "desired_date": payload.get("desired_date") or "",
        "flight_date": payload.get("flight_date") or "",
        "date_from": payload.get("date_from") or "",
        "date_to": payload.get("date_to") or "",
        "weight_kg": payload.get("weight_kg") or payload.get("capacity_kg") or "",
        "item_category_codes": payload.get("item_category_codes") or [],
        "excluded_category_codes": payload.get("excluded_category_codes") or [],
        "description": payload.get("description") or "",
        "author_username": (_form_value(data, "author_username") or "").strip().lstrip("@"),
        "item_request_id": post.item_request_id if post else None,
        "errors": {},
    }


def _draft_from_item(post: MarketPost, item, payload: dict, author: str) -> dict:
    draft = _draft_from_payload(post, payload)
    draft["item_request_id"] = item.pk
    draft["author_username"] = author or draft["author_username"]
    return draft


def _draft_description(post: MarketPost, payload: dict | None, *, origin_city: str, dest_city: str) -> str:
    payload = payload or {}
    is_supply = payload.get("type") == RequestType.SUPPLY
    return resolve_listing_description(
        post,
        str(payload.get("description") or ""),
        is_supply=is_supply,
        origin=payload.get("origin_city") or origin_city,
        dest=payload.get("destination_city") or dest_city,
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


def _apply_text_author(post: MarketPost) -> str:
    handle = _author_handle(post.text, post.channel_username)
    if handle and handle.lower() != (post.author_username or "").lower():
        if not post.author_username or post.author_username.lower() == (post.channel_username or "").lower():
            post.author_username = handle[:64]
            post.save(update_fields=["author_username", "updated_at"])
    return post.author_username or handle


def _author_handle(text: str, channel: str = "") -> str:
    channel_l = (channel or "").strip().lstrip("@").lower()
    for match in _HANDLE.finditer(text or ""):
        handle = match.group(1)
        if handle.lower() != channel_l:
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

