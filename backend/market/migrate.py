from __future__ import annotations

import logging
import zlib

from django.conf import settings
from django.utils import timezone

from ai.llm import llm_enabled
from item_requests.models import RequestStatus, RequestType
from item_requests.services import create_item_request, schedule_channel_sync, update_item_request
from market.catalog import catalog_location
from market.classify import classify_role, extract_route, is_courier_request
from market.dates import travel_date_for_post
from market.models import MarketPost, MarketRole
from market.review import (
    SKIP_AD,
    SKIP_DEFERRED,
    SKIP_LLM,
    SKIP_NOISE,
    fallback_listing_description,
    llm_review_limit,
    payload_from_review,
    review_market_post,
)
from market.rules import categories_for_request, cleaned_kg
from users.models import User

logger = logging.getLogger(__name__)

SKIP_ROLE = "unknown_role"
SKIP_ROUTE = "no_route"
SKIP_SAME_CITY = "same_city"
SKIP_CATALOG = "no_catalog_city"
SKIP_EXPIRED = "expired"
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


def migrate_market_posts() -> dict:
    created = updated = skipped = expired = deferred = 0
    budget = _ReviewBudget(llm_review_limit())
    for post in MarketPost.objects.order_by("posted_at", "telegram_message_id"):
        result = migrate_market_post(post, budget=budget)
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
    }


def migrate_market_post(
    post: MarketPost,
    *,
    owner: User | None = None,
    budget: _ReviewBudget | None = None,
    force_review: bool = False,
) -> str:
    payload, reason = clean_post(post, budget=budget, force_review=force_review)
    if payload is None:
        if reason == SKIP_DEFERRED:
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
            post.save(update_fields=["skip_reason", "migrated_at", "updated_at"])
            return "updated"
        item_request = create_item_request(owner, payload, imported=True, source_url=_source_url(post))
        post.item_request = item_request
        post.skip_reason = ""
        post.migrated_at = timezone.now()
        post.save(update_fields=["item_request", "skip_reason", "migrated_at", "updated_at"])
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
) -> tuple[dict | None, str | None]:
    if not force_review and (post.role == MarketRole.NOISE or classify_role(post.text) == MarketRole.NOISE):
        return None, SKIP_NOISE
    if not is_courier_request(post.text, post.role):
        return None, SKIP_ROLE
    if llm_enabled():
        return _clean_with_llm(post, budget=budget, force_review=force_review)
    return _clean_with_rules(post)


def _clean_with_llm(
    post: MarketPost,
    *,
    budget: _ReviewBudget | None,
    force_review: bool = False,
) -> tuple[dict | None, str | None]:
    from market.review import cached_review

    needs_call = force_review or cached_review(post) is None
    if needs_call and budget is not None and not budget.consume():
        return None, SKIP_DEFERRED
    review = review_market_post(post, force=force_review)
    payload, reason = payload_from_review(post, review)
    if payload is not None:
        return payload, None
    if review.error or reason == SKIP_LLM:
        fallback, _fallback_reason = _rules_fallback(post)
        if fallback is not None:
            logger.info(
                "LLM review failed for %s/%s (%s); inserting from regex rules and publishing.",
                post.channel_username,
                post.telegram_message_id,
                review.error or reason,
            )
            return fallback, None
    return None, reason


def _rules_fallback(post: MarketPost) -> tuple[dict | None, str | None]:
    guessed = classify_role(post.text)
    if guessed in {MarketRole.SUPPLY, MarketRole.DEMAND} and post.role != guessed:
        post.role = guessed
        post.save(update_fields=["role", "updated_at"])
    if post.role not in {MarketRole.SUPPLY, MarketRole.DEMAND}:
        return None, SKIP_ROLE
    return _clean_with_rules(post)


def _clean_with_rules(post: MarketPost) -> tuple[dict | None, str | None]:
    origin, dest = extract_route(post.text)
    origin_loc = catalog_location(origin)
    dest_loc = catalog_location(dest)
    if not origin_loc or not dest_loc:
        return None, SKIP_CATALOG if (origin or dest) else SKIP_ROUTE
    if origin_loc == dest_loc:
        return None, SKIP_SAME_CITY
    today = timezone.now().date()
    travel_date = travel_date_for_post(post.text, posted_at=post.posted_at, today=today)
    if travel_date is None:
        return None, SKIP_EXPIRED
    is_supply = post.role == MarketRole.SUPPLY
    carried, excluded = categories_for_request(post.text, is_supply=is_supply)
    kg = str(cleaned_kg(post.text))
    origin_country, origin_city = origin_loc
    dest_country, dest_city = dest_loc
    payload: dict = {
        "type": RequestType.SUPPLY if is_supply else RequestType.DEMAND,
        "origin_country": origin_country,
        "origin_city": origin_city,
        "destination_country": dest_country,
        "destination_city": dest_city,
        "item_category_codes": carried,
        "description": fallback_listing_description(
            is_supply=is_supply,
            origin=origin_city,
            dest=dest_city,
            category_codes=carried,
        ),
    }
    if is_supply:
        payload["capacity_kg"] = kg
        payload["flight_date"] = travel_date.isoformat()
        payload["date_from"] = travel_date.isoformat()
        payload["date_to"] = travel_date.isoformat()
        payload["excluded_category_codes"] = excluded
    else:
        payload["weight_kg"] = kg
        payload["desired_date"] = travel_date.isoformat()
    return payload, None


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
