from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from item_requests.models import ItemRequest, RequestStatus, RequestType
from item_requests.validation import assert_request_editable, validate_request_payload
from users.models import User

logger = logging.getLogger(__name__)


def expire_if_needed(item_request: ItemRequest) -> ItemRequest:
    if item_request.status == RequestStatus.ACTIVE and _request_is_due(item_request):
        item_request.status = RequestStatus.EXPIRED
        item_request.save(update_fields=["status", "updated_at"])
        from matching.services import expire_unfinished_matches_for_request

        expire_unfinished_matches_for_request(item_request)
        schedule_channel_sync(item_request)
    return item_request


def expire_user_requests(user: User) -> None:
    expire_due_requests(user=user)


def expire_due_requests(*, user: User | None = None, sync_channel: bool = True) -> int:
    now = timezone.now()
    expired_ids = list(due_to_expire_queryset(now=now, user=user).values_list("id", flat=True))
    if not expired_ids:
        return 0
    ItemRequest.objects.filter(id__in=expired_ids).update(status=RequestStatus.EXPIRED, updated_at=now)
    from matching.services import expire_unfinished_matches_for_request_ids

    expire_unfinished_matches_for_request_ids(expired_ids)
    if sync_channel:
        for request_id in expired_ids:
            schedule_channel_sync_id(request_id)
    return len(expired_ids)


def due_to_expire_queryset(*, now=None, user: User | None = None):
    now = now or timezone.now()
    today = now.date()
    queryset = ItemRequest.objects.filter(status=RequestStatus.ACTIVE).filter(
        Q(expires_at__lte=now)
        | Q(type=RequestType.DEMAND, desired_date__lt=today)
        | Q(type=RequestType.DEMAND, desired_date__isnull=True, date_to__lt=today)
        | Q(type=RequestType.SUPPLY, date_to__lt=today, flight_date__lt=today)
        | Q(type=RequestType.SUPPLY, date_to__lt=today, flight_date__isnull=True)
    )
    if user is not None:
        queryset = queryset.filter(user=user)
    return queryset


def _request_is_due(item_request: ItemRequest, *, now=None) -> bool:
    now = now or timezone.now()
    today = now.date()
    if item_request.expires_at and now >= item_request.expires_at:
        return True
    if item_request.type == RequestType.DEMAND:
        day = item_request.desired_date or item_request.date_to
        return bool(day and day < today)
    end = item_request.date_to
    flight = item_request.flight_date
    if end and end < today and (flight is None or flight < today):
        return True
    return False


def opposite_payload_from_listing(item: ItemRequest) -> dict:
    from django.core.exceptions import ValidationError

    category_codes = [category.code for category in item.item_categories.all()]
    kg = item.kg_value()
    if kg is None:
        field = "capacity_kg" if item.type == RequestType.SUPPLY else "weight_kg"
        raise ValidationError({field: "This listing has no weight."})
    payload = {
        "origin_country": item.origin_country,
        "origin_city": item.origin_city,
        "destination_country": item.destination_country,
        "destination_city": item.destination_city,
        "item_category_codes": category_codes,
    }
    if item.type == RequestType.SUPPLY:
        payload.update(
            {
                "type": RequestType.DEMAND,
                "desired_date": item.flight_date or item.date_from,
                "weight_kg": kg,
            }
        )
        return payload
    desired = item.desired_date or item.date_from
    payload.update(
        {
            "type": RequestType.SUPPLY,
            "date_from": item.date_from or desired,
            "date_to": item.date_to or desired,
            "flight_date": item.desired_date or item.date_to or desired,
            "capacity_kg": kg,
            "destination_cities": [
                {"country": country, "city": city} for country, city in item.destination_stop_pairs()
            ],
        }
    )
    return payload


def create_opposite_from_listing(user: User, item: ItemRequest) -> ItemRequest:
    return create_item_request(user, opposite_payload_from_listing(item))


def create_item_request(
    user: User,
    payload: dict,
    *,
    imported: bool = False,
    source_url: str = "",
    sync_channel: bool = True,
) -> ItemRequest:
    cleaned = validate_request_payload(payload)
    categories = cleaned.pop("item_categories")
    exclusions = cleaned.pop("excluded_categories")
    with transaction.atomic():
        item_request = ItemRequest.objects.create(
            user=user,
            imported=imported,
            source_url=(source_url or "").strip() if imported else "",
            **cleaned,
        )
        item_request.item_categories.set(categories)
        item_request.excluded_categories.set(exclusions)
    _sync_matches_quietly(item_request)
    if sync_channel:
        schedule_channel_sync(item_request)
    return item_request


@transaction.atomic
def update_item_request(item_request: ItemRequest, payload: dict) -> ItemRequest:
    expire_if_needed(item_request)
    assert_request_editable(item_request)
    cleaned = validate_request_payload(payload, partial=True, instance=item_request)
    categories = cleaned.pop("item_categories")
    exclusions = cleaned.pop("excluded_categories")
    for field, value in cleaned.items():
        setattr(item_request, field, value)
    item_request.save(update_fields=[*cleaned.keys(), "updated_at"])
    item_request.item_categories.set(categories)
    item_request.excluded_categories.set(exclusions)
    _sync_matches_quietly(item_request)
    schedule_channel_sync(item_request)
    return item_request


@transaction.atomic
def cancel_item_request(item_request: ItemRequest) -> ItemRequest:
    expire_if_needed(item_request)
    assert_request_editable(item_request)
    item_request.status = RequestStatus.CANCELLED
    item_request.package_sent = False
    item_request.save(update_fields=["status", "package_sent", "updated_at"])
    from matching.services import expire_unfinished_matches_for_request

    expire_unfinished_matches_for_request(item_request)
    schedule_channel_sync(item_request)
    return item_request


@transaction.atomic
def close_item_request(item_request: ItemRequest, *, package_sent: bool) -> ItemRequest:
    expire_if_needed(item_request)
    assert_request_editable(item_request)
    if item_request.type != RequestType.SUPPLY:
        from django.core.exceptions import ValidationError

        raise ValidationError({"type": "Only traveler requests can be closed with a trip outcome."})

    from matching.models import Match, MatchStatus
    from matching.services import expire_open_matches_for_request, expire_unfinished_matches_for_request

    if package_sent:
        connected = list(
            Match.objects.filter(
                Q(demand_request=item_request) | Q(supply_request=item_request),
                status=MatchStatus.CONNECTED,
            )
        )
        from matching.completion import complete_match

        for match in connected:
            complete_match(match, item_request.user)
        item_request.refresh_from_db()
        item_request.package_sent = True
        if item_request.status == RequestStatus.ACTIVE:
            item_request.status = RequestStatus.COMPLETED
            item_request.save(update_fields=["status", "package_sent", "updated_at"])
            expire_open_matches_for_request(item_request)
            schedule_channel_sync(item_request)
        else:
            item_request.save(update_fields=["package_sent", "updated_at"])
        return item_request

    item_request.status = RequestStatus.CANCELLED
    item_request.package_sent = False
    item_request.save(update_fields=["status", "package_sent", "updated_at"])
    expire_unfinished_matches_for_request(item_request)
    schedule_channel_sync(item_request)
    return item_request


def parse_package_sent(value) -> bool:
    from django.core.exceptions import ValidationError

    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "sent"}:
        return True
    if text in {"0", "false", "no", "close"}:
        return False
    raise ValidationError({"package_sent": "Tell us whether you sent the package."})


def _sync_matches_quietly(item_request: ItemRequest) -> None:
    try:
        from matching.services import sync_matches_for_request

        sync_matches_for_request(item_request)
    except Exception:
        logger.exception("Matching failed for request %s", item_request.pk)


def schedule_channel_sync(item_request: ItemRequest) -> None:
    if item_request.pk:
        schedule_channel_sync_id(item_request.pk)


def schedule_channel_sync_id(request_id: int) -> None:
    def _run() -> None:
        from notifications.channel import sync_request_channel

        sync_request_channel(request_id)

    transaction.on_commit(_run)
