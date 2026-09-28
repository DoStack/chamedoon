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
    claim_shadow_users(user)
    return user


def claim_shadow_users(user: User) -> int:
    """Move requests imported under this person's @username onto their real account."""
    from matching.contact import SYNTHETIC_TELEGRAM_USER_ID, is_person_username, is_shadow_user

    username = (user.telegram_username or "").strip()
    if is_shadow_user(user) or not is_person_username(username):
        return 0
    shadows = User.objects.filter(
        telegram_user_id__gte=SYNTHETIC_TELEGRAM_USER_ID,
        telegram_username__iexact=username,
    ).exclude(pk=user.pk)
    return sum(merge_shadow_user(shadow, user) for shadow in shadows)


@transaction.atomic
def merge_shadow_user(shadow: User, user: User) -> int:
    from matching.contact import is_shadow_user
    from matching.models import OPEN_MATCH_STATUSES, Match, MatchStatus

    if shadow.pk == user.pk or not is_shadow_user(shadow) or is_shadow_user(user):
        return 0
    moved = ItemRequest.objects.filter(user=shadow).update(user=user)
    Match.objects.filter(initiated_by=shadow).update(initiated_by=user)
    shadow.telegram_username = None
    shadow.is_active = False
    shadow.save(update_fields=["telegram_username", "is_active", "updated_at"])
    Match.objects.filter(
        demand_request__user=user,
        supply_request__user=user,
        status__in=OPEN_MATCH_STATUSES,
    ).update(status=MatchStatus.EXPIRED)
    return moved


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
