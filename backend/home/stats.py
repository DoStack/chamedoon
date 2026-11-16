"""What a listing's owner sees: unique views, saves and contacts taken. Never who.

Its own table, because `ContactReveal` writes no row while the contact gate is off. One row per person, listing and
kind, so a retry or a second visit never counts twice. The owner's own visits are not counted.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone

from home.models import HomeListing, HomeListingStat, StatKind
from home.ownership import owns

WEEK = timedelta(days=7)


def record(listing: HomeListing, telegram_id: int | None, kind: str) -> bool:
    """True when this person is new for this listing and kind."""
    if not telegram_id or owns(listing, telegram_id):
        return False
    try:
        with transaction.atomic():
            _row, created = HomeListingStat.objects.get_or_create(listing=listing, telegram_user_id=telegram_id, kind=kind)
    except IntegrityError:
        return False
    if created and kind == StatKind.CONTACT:
        from outreach.followups import contact_taken

        contact_taken(listing)
    return created


def insider_ids() -> set[int]:
    """Our own people: every adminyar admin. Their views and contacts are never anyone's numbers (audit 2026-11-16
    02:20 #460, rule 126: «۱ بازدید ... ۱ نفر اطلاعات تماستون رو گرفتن» was Mohammad's own check of the ad)."""
    from adminyar.models import AdminUser

    return {telegram_id for telegram_id in AdminUser.objects.values_list("telegram_user_id", flat=True) if telegram_id}


def counts(listing: HomeListing, *, since=None, outside: bool = False, exclude: tuple = ()) -> dict:
    """`outside`: only people who are not ours and not the owner, for any number we tell someone."""
    rows = HomeListingStat.objects.filter(listing=listing)
    if since is not None:
        rows = rows.filter(created_at__gte=since)
    if outside:
        from home.ownership import owner_telegram_id

        ours = insider_ids() | {int(telegram_id) for telegram_id in exclude if telegram_id}
        owner = owner_telegram_id(listing.post) if listing.post_id else None
        if owner:
            ours.add(int(owner))
        rows = rows.exclude(telegram_user_id__in=ours)
    found = dict(rows.values_list("kind").annotate(n=Count("id")).values_list("kind", "n"))
    return {"views": found.get(StatKind.VIEW, 0), "saves": found.get(StatKind.SAVE, 0), "contacts": found.get(StatKind.CONTACT, 0)}


def owner_stats(listing: HomeListing) -> dict:
    total = counts(listing)
    week = counts(listing, since=timezone.now() - WEEK)
    return {**total, **{f"{name}_7d": value for name, value in week.items()}}
