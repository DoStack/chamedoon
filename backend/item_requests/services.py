from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from item_requests.models import ItemRequest, RequestStatus
from item_requests.validation import assert_request_editable, validate_request_payload
from users.models import User


def expire_if_needed(item_request: ItemRequest) -> ItemRequest:
    if item_request.status == RequestStatus.ACTIVE and item_request.is_expired():
        item_request.status = RequestStatus.EXPIRED
        item_request.save(update_fields=["status", "updated_at"])
        from matching.services import expire_open_matches_for_request

        expire_open_matches_for_request(item_request)
        schedule_channel_sync(item_request)
    return item_request


def expire_user_requests(user: User) -> None:
    now = timezone.now()
    expired_ids = list(
        ItemRequest.objects.filter(user=user, status=RequestStatus.ACTIVE, expires_at__lte=now).values_list(
            "id", flat=True
        )
    )
    if not expired_ids:
        return
    ItemRequest.objects.filter(id__in=expired_ids).update(status=RequestStatus.EXPIRED, updated_at=now)
    from matching.services import expire_open_matches_for_request_ids

    expire_open_matches_for_request_ids(expired_ids)
    for request_id in expired_ids:
        schedule_channel_sync_id(request_id)


@transaction.atomic
def create_item_request(user: User, payload: dict) -> ItemRequest:
    cleaned = validate_request_payload(payload)
    categories = cleaned.pop("item_categories")
    exclusions = cleaned.pop("excluded_categories")
    item_request = ItemRequest.objects.create(user=user, **cleaned)
    item_request.item_categories.set(categories)
    item_request.excluded_categories.set(exclusions)
    from matching.services import sync_matches_for_request

    sync_matches_for_request(item_request)
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
    from matching.services import sync_matches_for_request

    sync_matches_for_request(item_request)
    schedule_channel_sync(item_request)
    return item_request


@transaction.atomic
def cancel_item_request(item_request: ItemRequest) -> ItemRequest:
    expire_if_needed(item_request)
    assert_request_editable(item_request)
    item_request.status = RequestStatus.CANCELLED
    item_request.save(update_fields=["status", "updated_at"])
    from matching.services import expire_open_matches_for_request

    expire_open_matches_for_request(item_request)
    schedule_channel_sync(item_request)
    return item_request


def schedule_channel_sync(item_request: ItemRequest) -> None:
    if item_request.pk:
        schedule_channel_sync_id(item_request.pk)


def schedule_channel_sync_id(request_id: int) -> None:
    def _run() -> None:
        from notifications.channel import sync_request_channel

        sync_request_channel(request_id)

    transaction.on_commit(_run)
