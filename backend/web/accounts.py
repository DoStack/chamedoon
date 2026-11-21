"""Email-only accounts and how one joins a Telegram account later.

Merge rule (an email account connects Telegram through «ورود با تلگرام», or a Telegram account verifies an email that
already has an email-only account): the Telegram account is kept, everything of the email account moves onto it.
- Rows with a foreign key to the user move. A row the Telegram account already has (a unique pair, for example the
  same saved listing twice) stays where it was: the target already holds that fact, so nothing is lost.
- Rows keyed by the Telegram id (alerts, chat state, app posts' author, courier chat...) move from the synthetic id to
  the real one, with the same rule for duplicates.
- The email moves to the Telegram account; the email account is deactivated, never deleted.
"""

from __future__ import annotations

import logging

from django.apps import apps
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from users.identity import is_email_id, new_email_id
from users.models import User

logger = logging.getLogger("web")

# Apps whose rows belong to a person by Telegram id. Not outreach (recipients of DMs) and not adminyar (staff).
ID_KEYED_APPS = {"home", "courier", "aisearch", "users", "item_requests", "matching", "support"}
ID_FIELDS = {"telegram_user_id", "author_id", "chat_id"}  # matched by the synthetic id only, so a channel id never moves


def normal_email(value: str) -> str:
    return (value or "").strip().lower()


def email_user(email: str) -> User:
    """The account of a verified email: the existing one (any kind), else a new email-only account."""
    email = normal_email(email)
    found = User.objects.filter(email=email).first()
    if found is not None:
        if found.email_verified_at is None:
            found.email_verified_at = timezone.now()
            found.save(update_fields=["email_verified_at", "updated_at"])
        return found
    for _ in range(5):
        try:
            with transaction.atomic():
                return User.objects.create(
                    telegram_user_id=new_email_id(),
                    first_name=(email.split("@")[0] or "کاربر")[:64],
                    email=email,
                    email_verified_at=timezone.now(),
                )
        except IntegrityError:
            found = User.objects.filter(email=email).first()
            if found is not None:
                return found
    raise RuntimeError("could not create an email account")


def _move(queryset, field: str, value) -> int:
    moved = 0
    for obj in queryset:
        try:
            with transaction.atomic():
                type(obj).objects.filter(pk=obj.pk).update(**{field: value})
            moved += 1
        except IntegrityError:
            continue  # the target already has this row
    return moved


def _id_keyed_fields():
    for model in apps.get_models():
        if model._meta.app_label not in ID_KEYED_APPS or model is User:
            continue
        for field in model._meta.concrete_fields:
            if field.name in ID_FIELDS and isinstance(field, models.BigIntegerField):
                yield model, field.name


@transaction.atomic
def merge_into(source: User, target: User) -> dict:
    """Move an email-only account onto a Telegram account. Returns counts for the log."""
    if source.pk == target.pk or not is_email_id(source.telegram_user_id) or is_email_id(target.telegram_user_id):
        return {}
    counts: dict[str, int] = {}
    for relation in User._meta.related_objects:
        if relation.many_to_many or not relation.field.concrete:
            continue
        model, field = relation.related_model, relation.field.name
        if model._meta.app_label == "web":
            continue
        moved = _move(model.objects.filter(**{field: source}), field, target)
        if moved:
            counts[f"{model._meta.label}.{field}"] = moved
    for model, field in _id_keyed_fields():
        moved = _move(model.objects.filter(**{field: source.telegram_user_id}), field, target.telegram_user_id)
        if moved:
            counts[f"{model._meta.label}.{field}"] = moved
    email, verified = source.email, source.email_verified_at
    source.email = None
    source.is_active = False
    source.save(update_fields=["email", "is_active", "updated_at"])
    if email and not target.email:
        target.email, target.email_verified_at = email, verified
        target.save(update_fields=["email", "email_verified_at", "updated_at"])
    logger.info("web_account_merged source=%s target=%s moved=%s", source.pk, target.pk, counts)
    return counts
