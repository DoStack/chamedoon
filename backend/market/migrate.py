from __future__ import annotations

import logging
import re
import time
import zlib

from datetime import date, datetime

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from ai.llm import llm_enabled
from item_requests.models import ItemRequest, RequestStatus, RequestType
from item_requests.services import create_item_request, schedule_channel_sync, update_item_request
from market.catalog import catalog_location, resolve_destination_locs
from market.classify import classify_role, extract_stops, extract_weight_kg, is_courier_request
from market.dates import is_past_travel_date, parse_travel_date, supply_travel_window, travel_date_for_post
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
SKIP_DUPLICATE_TRIP = "duplicate_trip"
DUPLICATE_DATE_SLACK_DAYS = 3
DUPLICATE_INFERRED_POSTED_DAYS = 14
SOURCE_USER_BASE = 9_000_000_000_000
DEFAULT_AUTHOR_FIRST_NAME = "کاربر عزیز"
EXPIRE_REASONS = {SKIP_AD, SKIP_NOISE, SKIP_ROLE, SKIP_EXPIRED, "incomplete"}
_HANDLE = re.compile(r"@([A-Za-z0-9_]{3,32})")


def ingest_owner() -> User:
    telegram_user_id = int(getattr(settings, "MARKET_INGEST_TELEGRAM_USER_ID", 1) or 1)
    user, created = User.objects.get_or_create(
        telegram_user_id=telegram_user_id,
        defaults={
            "first_name": _official_handle(),
            "telegram_username": None,
        },
    )
    if not created:
        _apply_author_first_name(user, user.telegram_username or _official_handle())
    return user


def author_identity(post: MarketPost, author_username: str = "") -> tuple[str, str]:
    channel = (post.channel_username or "").strip().lstrip("@")
    review = post.review_json if isinstance(post.review_json, dict) else {}
    candidates = [
        author_username,
        review.get("author_username"),
        post.author_username,
        *[match.group(1) for match in _HANDLE.finditer(post.text or "")],
    ]
    handle = ""
    for raw in candidates:
        candidate = _clean_handle(raw)
        if candidate and not _is_channel_handle(candidate, channel):
            handle = candidate
            break
    display = _author_display_name(post, handle=handle, channel=channel)
    return handle, display


def owner_for_post(post: MarketPost, author_username: str = "") -> User:
    from users.services import mark_market_extracted

    handle, display = author_identity(post, author_username)
    if handle:
        existing = User.objects.filter(telegram_username__iexact=handle).first()
        if existing:
            _apply_author_first_name(existing, display)
            return mark_market_extracted(existing)
        return _source_user(f"user:{handle.lower()}", first_name=display, username=handle)
    official = _official_handle()
    return _source_user(f"user:{official.lower()}", first_name=display, username=official[:32])


def find_duplicate_trip_request(
    owner: User,
    payload: dict,
    post: MarketPost,
    *,
    author_username: str = "",
) -> ItemRequest | None:
    handle, _display = author_identity(post, author_username)
    if not handle:
        return None
    request_type = payload.get("type")
    origin_country = str(payload.get("origin_country") or "").upper().strip()
    origin_city = str(payload.get("origin_city") or "").strip()
    if not request_type or not origin_country or not origin_city:
        return None
    candidates = (
        ItemRequest.objects.filter(
            user=owner,
            type=request_type,
            status=RequestStatus.ACTIVE,
            origin_country=origin_country,
            origin_city=origin_city,
        )
        .exclude(pk=post.item_request_id or 0)
        .order_by("-created_at")
    )
    new_dests = _payload_dest_pairs(payload)
    for existing in candidates:
        if not (_dest_pairs(existing) & new_dests):
            continue
        if _same_trip_dates(post, payload, existing):
            return existing
    return None


def _skip_duplicate_trip(post: MarketPost, existing: ItemRequest, payload: dict) -> str:
    try:
        _enrich_trip_request(existing, payload, post)
    except Exception:
        logger.exception(
            "Could not enrich duplicate trip %s from %s/%s",
            existing.pk,
            post.channel_username,
            post.telegram_message_id,
        )
    _mark_skip(post, SKIP_DUPLICATE_TRIP)
    post.migrated_at = timezone.now()
    post.llm_retry_started_at = None
    post.save(update_fields=["skip_reason", "migrated_at", "llm_retry_started_at", "updated_at"])
    logger.info(
        "Skipped %s/%s as duplicate of request #%s",
        post.channel_username,
        post.telegram_message_id,
        existing.pk,
    )
    return "duplicate"


def _enrich_trip_request(existing: ItemRequest, payload: dict, post: MarketPost) -> None:
    update: dict = {}
    dests = _ordered_union(_dest_pairs_list(existing), _payload_dest_list(payload))
    if dests and dests != _dest_pairs_list(existing):
        country, city = dests[-1]
        update["destination_country"] = country
        update["destination_city"] = city
        update["destination_cities"] = [{"country": item[0], "city": item[1]} for item in dests]
    new_parsed = parse_travel_date(post.text or "", posted_at=post.posted_at)
    old_post = _linked_market_post(existing)
    old_parsed = (
        parse_travel_date(old_post.text or "", posted_at=old_post.posted_at) if old_post else None
    )
    if new_parsed and old_parsed is None:
        if existing.type == RequestType.SUPPLY:
            if payload.get("flight_date"):
                update["flight_date"] = payload["flight_date"]
            if payload.get("date_from"):
                update["date_from"] = payload["date_from"]
            if payload.get("date_to"):
                update["date_to"] = payload["date_to"]
        elif payload.get("desired_date"):
            update["desired_date"] = payload["desired_date"]
    new_desc = str(payload.get("description") or "").strip()
    if new_desc and len(new_desc) > len((existing.description or "").strip()):
        update["description"] = new_desc
    if not update:
        return
    update_item_request(existing, update)


def _same_trip_dates(post: MarketPost, payload: dict, existing: ItemRequest) -> bool:
    new_parsed = parse_travel_date(post.text or "", posted_at=post.posted_at)
    old_post = _linked_market_post(existing)
    old_parsed = (
        parse_travel_date(old_post.text or "", posted_at=old_post.posted_at) if old_post else None
    )
    if new_parsed and old_parsed:
        return abs((new_parsed - old_parsed).days) <= DUPLICATE_DATE_SLACK_DAYS
    if new_parsed is None and old_parsed is None:
        old_posted = old_post.posted_at if old_post is not None else existing.created_at
        return abs((post.posted_at - old_posted).total_seconds()) <= DUPLICATE_INFERRED_POSTED_DAYS * 86400
    new_date = _payload_primary_date(payload)
    old_date = existing.flight_date or existing.desired_date
    if new_date and old_date and abs((new_date - old_date).days) <= DUPLICATE_DATE_SLACK_DAYS:
        return True
    new_from, new_to = _payload_range(payload)
    return _ranges_overlap(new_from, new_to, existing.date_from, existing.date_to)


def _payload_dest_list(payload: dict) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []
    for item in payload.get("destination_cities") or []:
        if not isinstance(item, dict):
            continue
        country = str(item.get("country") or "").upper().strip()
        city = str(item.get("city") or item.get("slug") or "").strip()
        pair = (country, city)
        if country and city and pair not in seen:
            seen.append(pair)
    country = str(payload.get("destination_country") or "").upper().strip()
    city = str(payload.get("destination_city") or "").strip()
    pair = (country, city)
    if country and city and pair not in seen:
        seen.append(pair)
    return seen


def _payload_dest_pairs(payload: dict) -> set[tuple[str, str]]:
    return set(_payload_dest_list(payload))


def _dest_pairs(item: ItemRequest) -> set[tuple[str, str]]:
    return set(_dest_pairs_list(item))


def _dest_pairs_list(item: ItemRequest) -> list[tuple[str, str]]:
    return [(country, city) for country, city in item.destination_stop_pairs()]


def _ordered_union(first: list[tuple[str, str]], extra: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []
    for pair in [*first, *extra]:
        if pair not in seen:
            seen.append(pair)
    return seen


def _payload_primary_date(payload: dict) -> date | None:
    if payload.get("type") == RequestType.SUPPLY:
        return _parse_iso(payload.get("flight_date"))
    return _parse_iso(payload.get("desired_date"))


def _payload_range(payload: dict) -> tuple[date | None, date | None]:
    if payload.get("type") == RequestType.SUPPLY:
        start = _parse_iso(payload.get("date_from")) or _parse_iso(payload.get("flight_date"))
        end = _parse_iso(payload.get("date_to")) or _parse_iso(payload.get("flight_date"))
        return start, end
    day = _parse_iso(payload.get("desired_date"))
    return day, day


def _parse_iso(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _ranges_overlap(start_a: date | None, end_a: date | None, start_b: date | None, end_b: date | None) -> bool:
    if None in (start_a, end_a, start_b, end_b):
        return False
    if start_a > end_a:
        start_a, end_a = end_a, start_a
    if start_b > end_b:
        start_b, end_b = end_b, start_b
    return start_a <= end_b and start_b <= end_a


def _linked_market_post(item: ItemRequest) -> MarketPost | None:
    try:
        return item.market_post
    except ObjectDoesNotExist:
        return None


def normalize_user_first_names() -> dict:
    return _normalize_first_names(User)


def _normalize_first_names(user_model) -> dict:
    updated = 0
    for user in user_model.objects.iterator():
        if not _should_replace_first_name(user):
            continue
        replacement = _preferred_first_name("", user.telegram_username)
        if not replacement or user.first_name == replacement:
            continue
        user.first_name = replacement
        user.save(update_fields=["first_name", "updated_at"])
        updated += 1
    return {"ok": True, "updated": updated}


def reassign_imported_request_owners() -> dict:
    updated = 0
    posts = MarketPost.objects.filter(item_request__isnull=False).select_related("item_request")
    for post in posts.iterator():
        request = post.item_request
        if request is None:
            continue
        owner = owner_for_post(post)
        _store_author_handle(post)
        if request.user_id != owner.id:
            request.user = owner
            request.save(update_fields=["user", "updated_at"])
            updated += 1
    return {"ok": True, "updated": updated}


def _clean_handle(value) -> str:
    return (str(value or "")).strip().lstrip("@")[:32]


def _is_channel_handle(handle: str, channel: str = "") -> bool:
    needle = _clean_handle(handle).lower()
    if not needle:
        return False
    from market.ingest import market_channel_usernames

    sources = {name.lower() for name in market_channel_usernames()}
    source = _clean_handle(channel).lower()
    if source:
        sources.add(source)
    return needle in sources


def _official_handle() -> str:
    channel = _clean_handle(getattr(settings, "TELEGRAM_CHANNEL_USERNAME", "") or "")
    return channel or "koolbar"


def _author_display_name(post: MarketPost, *, handle: str, channel: str) -> str:
    name = (post.author_name or "").strip()[:64]
    if (
        name
        and not _is_placeholder_first_name(name)
        and not _is_channel_handle(name, channel)
        and name.lower() != handle.lower()
        and len(name) <= 32
    ):
        return name
    return handle


def _placeholder_first_names() -> set[str]:
    names = {
        "",
        "channel listing",
        "koolbar",
        DEFAULT_AUTHOR_FIRST_NAME.lower(),
        "ارسال بار به سراسر دنیا",
    }
    official = _official_handle().lower()
    if official:
        names.add(official)
    from market.ingest import market_channel_usernames

    names.update(name.lower() for name in market_channel_usernames())
    names.update(_channel_titles())
    return names


def _channel_titles() -> set[str]:
    from market.ingest import market_channel_usernames

    channels = {name.lower() for name in market_channel_usernames()}
    official = _official_handle().lower()
    if official:
        channels.add(official)
    titles: set[str] = set()
    posts = MarketPost.objects.exclude(author_name="").only(
        "author_name",
        "author_username",
        "channel_username",
    )
    for post in posts.iterator():
        handle = _clean_handle(post.author_username).lower()
        channel = _clean_handle(post.channel_username).lower()
        if handle and handle not in channels and handle != channel:
            continue
        title = (post.author_name or "").strip().lower()
        if title:
            titles.add(title)
    return titles


def _is_placeholder_first_name(name: str) -> bool:
    cleaned = (name or "").strip()
    if not cleaned:
        return True
    return cleaned.lower() in _placeholder_first_names() or _is_channel_handle(cleaned)


def _should_replace_first_name(user: User) -> bool:
    return _is_placeholder_first_name(user.first_name or "")


def _preferred_first_name(display: str, username: str | None) -> str:
    display = (display or "").strip()[:64]
    if display and not _is_placeholder_first_name(display):
        return display
    handle = (username or "").strip().lstrip("@")[:64]
    if handle:
        return handle
    return display


def _apply_author_first_name(user: User, display: str) -> None:
    display = (display or "").strip()[:64]
    username = (user.telegram_username or "").strip()
    person_name = bool(
        display
        and not _is_placeholder_first_name(display)
        and display.lower() != username.lower()
    )
    if person_name:
        target = display
    elif _should_replace_first_name(user):
        target = _preferred_first_name(display, username)
    else:
        return
    if not target or user.first_name == target:
        return
    user.first_name = target
    user.save(update_fields=["first_name", "updated_at"])


def _store_author_handle(post: MarketPost) -> str:
    handle, _display = author_identity(post)
    if handle and _clean_handle(post.author_username).lower() != handle.lower():
        post.author_username = handle[:64]
        post.save(update_fields=["author_username", "updated_at"])
    return handle


def migrate_market_posts(
    *,
    pending_llm_only: bool = False,
    stop_at: float | None = None,
    wait_for_llm: bool = True,
    pending_only: bool = False,
    newest_first: bool = False,
    max_posts: int = 0,
    posted_after=None,
    sync_channel: bool = True,
) -> dict:
    created = updated = skipped = expired = deferred = duplicates = 0
    truncated = False
    budget = _ReviewBudget(llm_review_limit())
    posts = MarketPost.objects.select_related("item_request")
    if newest_first:
        posts = posts.order_by("-posted_at", "-telegram_message_id")
    else:
        posts = posts.order_by("posted_at", "telegram_message_id")
    if pending_llm_only:
        posts = posts.filter(item_request__isnull=True, llm_retry_started_at__isnull=False)
    elif pending_only:
        posts = posts.filter(item_request__isnull=True, skip_reason="")
    if posted_after is not None:
        posts = posts.filter(posted_at__gte=posted_after)
    processed = 0
    for post in posts.iterator():
        if stop_at is not None and time.monotonic() >= stop_at:
            truncated = True
            break
        if max_posts and processed >= max_posts:
            truncated = True
            break
        processed += 1
        result = migrate_market_post(
            post,
            budget=budget,
            force_review=pending_llm_only,
            wait_for_llm=wait_for_llm,
            sync_channel=sync_channel,
        )
        if result == "created":
            created += 1
        elif result == "updated":
            updated += 1
        elif result == "expired":
            expired += 1
        elif result == "deferred":
            deferred += 1
        elif result == "duplicate":
            duplicates += 1
            skipped += 1
        else:
            skipped += 1
    return {
        "ok": True,
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "duplicates": duplicates,
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
    sync_channel: bool = True,
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

    owner = owner or owner_for_post(post)
    _store_author_handle(post)
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
        duplicate = find_duplicate_trip_request(owner, payload, post)
        if duplicate is not None:
            return _skip_duplicate_trip(post, duplicate, payload)
        item_request = create_item_request(
            owner,
            payload,
            imported=True,
            source_url=_source_url(post),
            sync_channel=sync_channel,
        )
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
    if wait_for_llm and llm_enabled():
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
    from users.services import mark_market_extracted

    first_name = _preferred_first_name(first_name, username) or (username or _official_handle())[:64]
    telegram_user_id = SOURCE_USER_BASE + (zlib.crc32(seed.encode("utf-8")) & 0xFFFFFFFF)
    user, created = User.objects.get_or_create(
        telegram_user_id=telegram_user_id,
        defaults={
            "first_name": first_name,
            "telegram_username": username,
            "from_market": True,
        },
    )
    if not created:
        _apply_author_first_name(user, first_name)
        if username and user.telegram_username != username:
            user.telegram_username = username
            user.save(update_fields=["telegram_username", "updated_at"])
        mark_market_extracted(user)
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
