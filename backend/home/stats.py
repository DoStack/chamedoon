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


def counts(listing: HomeListing, *, since=None) -> dict:
    rows = HomeListingStat.objects.filter(listing=listing)
    if since is not None:
        rows = rows.filter(created_at__gte=since)
    found = dict(rows.values_list("kind").annotate(n=Count("id")).values_list("kind", "n"))
    return {"views": found.get(StatKind.VIEW, 0), "saves": found.get(StatKind.SAVE, 0), "contacts": found.get(StatKind.CONTACT, 0)}


def owner_stats(listing: HomeListing) -> dict:
    total = counts(listing)
    week = counts(listing, since=timezone.now() - WEEK)
    return {**total, **{f"{name}_7d": value for name, value in week.items()}}
