from __future__ import annotations

from django.db import transaction

from item_requests.models import ItemRequest, RequestStatus
from users.models import User
from users.telegram import TelegramIdentity


def upsert_telegram_user(identity: TelegramIdentity) -> User:
    user, _created = User.objects.update_or_create(
        telegram_user_id=identity.telegram_user_id,
        defaults={
            "telegram_username": identity.telegram_username,
            "first_name": identity.first_name,
            "last_name": identity.last_name,
        },
    )
    return user


@transaction.atomic
def deactivate_user(user: User) -> User:
    user.is_active = False
    user.save(update_fields=["is_active", "updated_at"])
    from item_requests.services import cancel_item_request

    active = list(ItemRequest.objects.filter(user=user, status=RequestStatus.ACTIVE))
    for item_request in active:
        cancel_item_request(item_request)
    return user


@transaction.atomic
def activate_user(user: User) -> User:
    user.is_active = True
    user.save(update_fields=["is_active", "updated_at"])
    return user
