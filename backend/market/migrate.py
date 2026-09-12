from __future__ import annotations

import logging
import time
import zlib

from django.conf import settings
from django.utils import timezone

from ai.llm import llm_enabled
from item_requests.models import RequestStatus, RequestType
from item_requests.services import create_item_request, schedule_channel_sync, update_item_request
from market.catalog import catalog_location, resolve_destination_locs
from market.classify import classify_role, extract_stops, extract_weight_kg, is_courier_request
from market.dates import is_past_travel_date, supply_travel_window, travel_date_for_post
from market.models import MarketPost, MarketRole
from market.review import (
    SKIP_AD,
    SKIP_DEFERRED,
    SKIP_LLM,
    SKIP_NOISE,
    extract_contact_phone,
    fallback_listing_description,
    llm_review_limit,
    payload_from_review,
    review_market_post,
)
from item_requests.weights import suggested_kg
from market.rules import DEFAULT_KG, categories_for_request, cleaned_kg
from users.models import User

logger = logging.getLogger(__name__)

SKIP_ROLE = "unknown_role"
SKIP_ROUTE = "no_route"
SKIP_SAME_CITY = "same_city"
SKIP_CATALOG = "no_catalog_city"
SKIP_EXPIRED = "expired"
SKIP_LLM_RETRY = "llm_retry"
SOURCE_USER_BASE = 9_000_000_000_000
EXPIRE_REASONS = {SKIP_AD, SKIP_NOISE, SKIP_ROLE, SKIP_EXPIRED, "incomplete"}


def ingest_owner() -> User:
    telegram_user_id = int(getattr(settings, "MARKET_INGEST_TELEGRAM_USER_ID", 1) or 1)
    user, _created = User.objects.get_or_create(
        telegram_user_id=telegram_user_id,
        defaults={
            "first_name": "Channel listing",
            "telegram_username": None,
        },
    )
    return user


def owner_for_post(post: MarketPost, author_username: str = "") -> User:
    handle = (author_username or post.author_username or "").strip().lstrip("@")[:32]
    channel = (post.channel_username or "").strip().lstrip("@")
    if handle and handle.lower() != channel.lower():
        existing = User.objects.filter(telegram_username__iexact=handle).first()
        if existing:
            return existing
        return _source_user(f"user:{handle.lower()}", first_name=handle, username=handle)
    display = (post.author_name or channel or "Channel listing").strip()[:64] or "Channel listing"
    username = channel[:32] or None
    return _source_user(f"channel:{channel.lower()}", first_name=display, username=username)


def migrate_market_posts(
    *,
    pending_llm_only: bool = False,
    stop_at: float | None = None,
    wait_for_llm: bool = True,
) -> dict:
    created = updated = skipped = expired = deferred = 0
    truncated = False
    budget = _ReviewBudget(llm_review_limit())
    posts = MarketPost.objects.order_by("posted_at", "telegram_message_id")
    if pending_llm_only:
        posts = posts.filter(item_request__isnull=True, llm_retry_started_at__isnull=False)
    for post in posts:
        if stop_at is not None and time.monotonic() >= stop_at:
            truncated = True
            break
        result = migrate_market_post(
            post,
            budget=budget,
            force_review=pending_llm_only,
            wait_for_llm=wait_for_llm,
        )
        if result == "created":
            created += 1
        elif result == "updated":
            updated += 1
        elif result == "expired":
            expired += 1
        elif result == "deferred":
            deferred += 1
        else:
            skipped += 1
    return {
        "ok": True,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "expired": expired,
        "deferred": deferred,
        "llm_reviews": budget.used,
        "truncated": truncated,
    }


def migrate_market_post(
    post: MarketPost,
    *,
    owner: User | None = None,
    budget: _ReviewBudget | None = None,
    force_review: bool = False,
    wait_for_llm: bool = True,
) -> str:
    payload, reason = clean_post(
        post,
        budget=budget,
        force_review=force_review,
        wait_for_llm=wait_for_llm,
    )
    if payload is None:
        if reason in {SKIP_DEFERRED, SKIP_LLM_RETRY}:
            if reason == SKIP_LLM_RETRY:
                _mark_skip(post, SKIP_LLM_RETRY)
            return "deferred"
        _mark_skip(post, reason or SKIP_ROLE)
        if reason in EXPIRE_REASONS:
            expired = _expire_linked(post)
            if expired:
                return "expired"
        return "skipped"

    owner = owner or owner_for_post(post, str((post.review_json or {}).get("author_username") or ""))
    try:
        if post.item_request_id:
            existing = post.item_request
            if existing.status != RequestStatus.ACTIVE:
                return "skipped"
            update_payload = {key: value for key, value in payload.items() if key != "type"}
            update_item_request(existing, update_payload)
            if existing.user_id != owner.id:
                existing.user = owner
                existing.save(update_fields=["user", "updated_at"])
            _apply_source_url(existing, post)
            post.skip_reason = ""
            post.migrated_at = timezone.now()
            post.llm_retry_started_at = None
            post.save(update_fields=["skip_reason", "migrated_at", "llm_retry_started_at", "updated_at"])
            return "updated"
        item_request = create_item_request(owner, payload, imported=True, source_url=_source_url(post))
        post.item_request = item_request
        post.skip_reason = ""
        post.migrated_at = timezone.now()
        post.llm_retry_started_at = None
        post.save(update_fields=["item_request", "skip_reason", "migrated_at", "llm_retry_started_at", "updated_at"])
        return "created"
    except Exception:
        logger.exception("Market migrate failed for %s/%s", post.channel_username, post.telegram_message_id)
        _mark_skip(post, "validation")
        return "skipped"


def clean_post(
    post: MarketPost,
    *,
    budget: _ReviewBudget | None = None,
    force_review: bool = False,
    wait_for_llm: bool = True,
) -> tuple[dict | None, str | None]:
    if not force_review and (post.role == MarketRole.NOISE or classify_role(post.text) == MarketRole.NOISE):
        return None, SKIP_NOISE
    if not is_courier_request(post.text, post.role):
        return None, SKIP_ROLE
    if llm_enabled():
        return _clean_with_llm(
            post,
            budget=budget,
            force_review=force_review,
            wait_for_llm=wait_for_llm,
        )
    return _rules_fallback(post)


def _clean_with_llm(
    post: MarketPost,
    *,
    budget: _ReviewBudget | None,
    force_review: bool = False,
    wait_for_llm: bool = True,
) -> tuple[dict | None, str | None]:
    from market.review import cached_review

    needs_call = force_review or cached_review(post) is None
    if needs_call and budget is not None and not budget.consume():
        return None, SKIP_DEFERRED
    review = review_market_post(post, force=force_review)
    payload, reason = payload_from_review(post, review)
    if payload is not None:
        _clear_llm_retry(post)
        return payload, None
    if review.error or reason == SKIP_LLM:
        if not wait_for_llm or _llm_retry_expired(post):
            fallback, _fallback_reason = _rules_fallback(post)
            if fallback is not None:
                _clear_llm_retry(post)
                logger.info(
                    "LLM review failed for %s/%s (%s); inserting from regex rules and publishing.",
                    post.channel_username,
                    post.telegram_message_id,
                    review.error or reason,
                )
                return fallback, None
            return None, _fallback_reason
        _mark_llm_retry(post)
        logger.info(
            "LLM review failed for %s/%s (%s); retrying for up to %sh before using regex rules.",
            post.channel_username,
            post.telegram_message_id,
            review.error or reason,
            llm_retry_hours(),
        )
        return None, SKIP_LLM_RETRY
    return None, reason


def llm_retry_hours() -> int:
    try:
        return max(1, int(getattr(settings, "MARKET_LLM_RETRY_HOURS", 6) or 6))
    except (TypeError, ValueError):
        return 6


def _mark_llm_retry(post: MarketPost) -> None:
    fields = ["skip_reason", "updated_at"]
    post.skip_reason = SKIP_LLM_RETRY
    if post.llm_retry_started_at is None:
        post.llm_retry_started_at = timezone.now()
        fields.append("llm_retry_started_at")
    post.save(update_fields=fields)


def _clear_llm_retry(post: MarketPost) -> None:
    if post.llm_retry_started_at is None and post.skip_reason != SKIP_LLM_RETRY:
        return
    post.llm_retry_started_at = None
    if post.skip_reason == SKIP_LLM_RETRY:
        post.skip_reason = ""
    post.save(update_fields=["llm_retry_started_at", "skip_reason", "updated_at"])


def _llm_retry_expired(post: MarketPost) -> bool:
    started = post.llm_retry_started_at
    if started is None:
        return False
    elapsed = timezone.now() - started
    return elapsed.total_seconds() >= llm_retry_hours() * 3600


def _rules_fallback(post: MarketPost) -> tuple[dict | None, str | None]:
    guessed = classify_role(post.text)
    if guessed in {MarketRole.SUPPLY, MarketRole.DEMAND} and post.role != guessed:
        post.role = guessed
        post.save(update_fields=["role", "updated_at"])
    if post.role not in {MarketRole.SUPPLY, MarketRole.DEMAND}:
        return None, SKIP_ROLE
    return _clean_with_rules(post)


def _clean_with_rules(post: MarketPost, *, date_fallback: bool = False) -> tuple[dict | None, str | None]:
    origin, dests = extract_stops(post.text)
    origin_loc = catalog_location(origin)
    dest_locs = resolve_destination_locs(dests, origin_loc=origin_loc)
    dest_loc = dest_locs[-1] if dest_locs else None
    if not origin_loc or not dest_loc:
        return None, SKIP_CATALOG if (origin or dests) else SKIP_ROUTE
    if origin_loc == dest_loc:
        return None, SKIP_SAME_CITY
    guessed = classify_role(post.text)
    if guessed in {MarketRole.SUPPLY, MarketRole.DEMAND} and post.role != guessed:
        post.role = guessed
        post.save(update_fields=["role", "updated_at"])
    today = timezone.now().date()
    is_supply = post.role == MarketRole.SUPPLY
    if is_past_travel_date(post.text, posted_at=post.posted_at, today=today):
        return None, SKIP_EXPIRED
    if is_supply:
        window = supply_travel_window(post.text, posted_at=post.posted_at, today=today)
        if window is None and date_fallback:
            window = supply_travel_window("", posted_at=post.posted_at, today=today)
        if window is None:
            return None, SKIP_EXPIRED
    else:
        travel_date = travel_date_for_post(post.text, posted_at=post.posted_at, today=today)
        if travel_date is None and date_fallback:
            travel_date = today
        if travel_date is None:
            return None, SKIP_EXPIRED
    carried, excluded = categories_for_request(post.text, is_supply=is_supply)
    if not carried and not date_fallback:
        carried = ["DOCUMENTS"]
    kg = _payload_kg(post.text, carried, is_supply=is_supply, date_fallback=date_fallback)
    origin_country, origin_city = origin_loc
    dest_country, dest_city = dest_loc
    dest_slugs = [city for _country, city in dest_locs]
    payload: dict = {
        "type": RequestType.SUPPLY if is_supply else RequestType.DEMAND,
        "origin_country": origin_country,
        "origin_city": origin_city,
        "destination_country": dest_country,
        "destination_city": dest_city,
        "destination_cities": [{"country": country, "city": city} for country, city in dest_locs],
        "item_category_codes": carried,
        "description": fallback_listing_description(
            is_supply=is_supply,
            origin=origin_city,
            dest=dest_city,
            dests=dest_slugs,
            dest_pairs=dest_locs,
            category_codes=carried,
            contact_phone=extract_contact_phone(post.text),
        ),
    }
    if is_supply:
        if kg:
            payload["capacity_kg"] = kg
        payload["flight_date"] = window["flight_date"].isoformat()
        payload["date_from"] = window["date_from"].isoformat()
        payload["date_to"] = window["date_to"].isoformat()
        payload["excluded_category_codes"] = excluded
    else:
        if kg:
            payload["weight_kg"] = kg
        payload["desired_date"] = travel_date.isoformat()
    return payload, None


def _payload_kg(text: str, carried: list[str], *, is_supply: bool, date_fallback: bool) -> str:
    if extract_weight_kg(text) is not None:
        return str(cleaned_kg(text, carried))
    if is_supply:
        return "" if date_fallback else str(suggested_kg(carried) or DEFAULT_KG)
    suggested = suggested_kg(carried)
    if suggested is not None:
        return str(suggested)
    return "" if date_fallback else str(DEFAULT_KG)


def _source_user(seed: str, *, first_name: str, username: str | None) -> User:
    telegram_user_id = SOURCE_USER_BASE + (zlib.crc32(seed.encode("utf-8")) & 0xFFFFFFFF)
    user, created = User.objects.get_or_create(
        telegram_user_id=telegram_user_id,
        defaults={
            "first_name": first_name[:64],
            "telegram_username": username,
        },
    )
    if not created:
        fields: list[str] = []
        if user.first_name != first_name[:64]:
            user.first_name = first_name[:64]
            fields.append("first_name")
        if username and user.telegram_username != username:
            user.telegram_username = username
            fields.append("telegram_username")
        if fields:
            user.save(update_fields=[*fields, "updated_at"])
    return user


def _expire_linked(post: MarketPost) -> bool:
    linked = post.item_request
    if linked is None or linked.status != RequestStatus.ACTIVE:
        return False
    linked.status = RequestStatus.EXPIRED
    linked.save(update_fields=["status", "updated_at"])
    schedule_channel_sync(linked)
    return True


def _apply_source_url(item_request, post: MarketPost) -> None:
    url = _source_url(post)
    if not url or item_request.source_url == url:
        return
    item_request.source_url = url
    item_request.save(update_fields=["source_url", "updated_at"])


def _source_url(post: MarketPost) -> str:
    url = (post.source_url or "").strip()
    if url:
        return url[:255]
    channel = (post.channel_username or "").strip().lstrip("@")
    if channel and post.telegram_message_id:
        return f"https://t.me/{channel}/{post.telegram_message_id}"
    return ""


def _mark_skip(post: MarketPost, reason: str) -> None:
    post.skip_reason = reason
    post.save(update_fields=["skip_reason", "updated_at"])


class _ReviewBudget:
    def __init__(self, limit: int):
        self.limit = limit
        self.used = 0

    def consume(self) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
        return True
