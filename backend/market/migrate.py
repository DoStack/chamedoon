from __future__ import annotations

import logging

from django.conf import settings
from django.utils import timezone

from item_requests.models import RequestStatus, RequestType
from item_requests.services import create_item_request, update_item_request
from market.catalog import catalog_location
from market.classify import extract_route
from market.dates import travel_date_for_post
from market.models import MarketPost, MarketRole
from market.rules import categories_for_request, cleaned_kg
from users.models import User

logger = logging.getLogger(__name__)

SKIP_NOISE = "noise"
SKIP_ROLE = "unknown_role"
SKIP_ROUTE = "no_route"
SKIP_SAME_CITY = "same_city"
SKIP_CATALOG = "no_catalog_city"
SKIP_EXPIRED = "expired"
DESCRIPTION_MAX = 2000


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


def migrate_market_posts() -> dict:
    owner = ingest_owner()
    created = updated = skipped = expired = 0
    for post in MarketPost.objects.order_by("posted_at", "telegram_message_id"):
        result = migrate_market_post(post, owner=owner)
        if result == "created":
            created += 1
        elif result == "updated":
            updated += 1
        elif result == "expired":
            expired += 1
        else:
            skipped += 1
    return {
        "ok": True,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "expired": expired,
    }


def migrate_market_post(post: MarketPost, *, owner: User | None = None) -> str:
    owner = owner or ingest_owner()
    payload, reason = clean_post(post)
    if payload is None:
        _mark_skip(post, reason or SKIP_ROLE)
        linked = post.item_request
        if linked and linked.status == RequestStatus.ACTIVE and reason == SKIP_EXPIRED:
            linked.status = RequestStatus.EXPIRED
            linked.save(update_fields=["status", "updated_at"])
            return "expired"
        return "skipped"

    try:
        if post.item_request_id:
            existing = post.item_request
            if existing.status != RequestStatus.ACTIVE:
                return "skipped"
            update_payload = {key: value for key, value in payload.items() if key != "type"}
            update_item_request(existing, update_payload)
            post.skip_reason = ""
            post.migrated_at = timezone.now()
            post.save(update_fields=["skip_reason", "migrated_at", "updated_at"])
            return "updated"
        item_request = create_item_request(owner, payload, imported=True)
        post.item_request = item_request
        post.skip_reason = ""
        post.migrated_at = timezone.now()
        post.save(update_fields=["item_request", "skip_reason", "migrated_at", "updated_at"])
        return "created"
    except Exception:
        logger.exception("Market migrate failed for %s/%s", post.channel_username, post.telegram_message_id)
        _mark_skip(post, "validation")
        return "skipped"


def clean_post(post: MarketPost) -> tuple[dict | None, str | None]:
    if post.role == MarketRole.NOISE:
        return None, SKIP_NOISE
    if post.role not in {MarketRole.SUPPLY, MarketRole.DEMAND}:
        return None, SKIP_ROLE
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
        "description": (post.text or "")[:DESCRIPTION_MAX],
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


def _mark_skip(post: MarketPost, reason: str) -> None:
    post.skip_reason = reason
    post.save(update_fields=["skip_reason", "updated_at"])
