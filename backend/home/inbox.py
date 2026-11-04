"""F17: what lands in a person's inbox. Writing waits for the account flag, and never fails the step that called it."""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from home import conf
from home.models import HomeListing, HomeNotification, HomeSaved, ListingStatus, NotificationKind
from home.public import GONE, follow_merged, listing_state
from users.models import User

logger = logging.getLogger(__name__)

OWN_KINDS = {NotificationKind.LISTING_LIVE, NotificationKind.LISTING_HELD, NotificationKind.LISTING_REJECTED, NotificationKind.LISTING_EXPIRED}


def _open_for(user_id: int) -> bool:
    current = conf.row()
    if current.account_api_on:
        return True
    return bool(conf.listed_ids(current.qa_user_ids)) and conf.qa_user(User.objects.filter(pk=user_id).values_list("telegram_user_id", flat=True).first())


def write(user_id: int | None, kind: str, *, listing: HomeListing | None = None, alert=None, title: str = "", match_count: int | None = None) -> None:
    if not user_id:
        return
    try:
        if not _open_for(user_id):
            return
        with transaction.atomic():
            HomeNotification.objects.create(user_id=user_id, kind=kind, listing=listing, alert=alert, title=title[:160], match_count=match_count)
    except Exception:  # noqa: BLE001 - a lost inbox line must not stop a post or a send
        logger.exception("inbox_write_failed kind=%s", kind)


def owner_id(listing: HomeListing) -> int | None:
    from home.alerts import find_user

    author = listing.post.author_id
    user = find_user(author) if author else None
    return user.pk if user else None


def listing_closed(listing: HomeListing, *, expired: bool = False) -> None:
    """People who saved it hear it closed; its owner too when nobody confirmed it. The person who closed it saved nothing to hear about."""
    try:
        if not conf.inbox_may_write():
            return
        from home.freshness import listing_title

        with transaction.atomic():
            title = listing_title(listing)
            owner = owner_id(listing)
            savers = list(HomeSaved.objects.filter(listing=listing).exclude(user_id=owner).values_list("user_id", flat=True))
    except Exception:  # noqa: BLE001 - a lost inbox line must not stop a listing closing
        logger.exception("inbox_closed_failed listing=%s", listing.pk)
        return
    if expired and owner:
        write(owner, NotificationKind.LISTING_EXPIRED, listing=listing, title=title)
    for user_id in savers:
        write(user_id, NotificationKind.SAVED_CLOSED, listing=listing, title=title)


def target_state(row: HomeNotification) -> str:
    """Where the line can go. An owner opens their own listing even while review holds it."""
    if row.kind == NotificationKind.ALERT_MATCH:
        return "active" if row.alert_id else GONE
    listing = follow_merged(row.listing) if row.listing_id else None
    if listing is None or listing.status == ListingStatus.REMOVED:
        return GONE
    if row.kind in OWN_KINDS and listing.needs_review:
        return "active"
    return listing_state(listing)


def payload(row: HomeNotification) -> dict:
    return {
        "id": row.pk,
        "notification_kind": row.kind,
        "listing_id": row.listing_id,
        "alert_id": row.alert_id,
        "title": row.title,
        "match_count": row.match_count,
        "reject_code": row.reject_code or None,
        "target_state": target_state(row),
        "created_at": row.created_at,
        "read_at": row.read_at,
    }


def unread(user) -> int:
    return HomeNotification.objects.filter(user=user, read_at__isnull=True).count()


def purge_old(*, now=None) -> int:
    cut = (now or timezone.now()) - timedelta(days=HomeNotification.RETENTION_DAYS)
    deleted, _rows = HomeNotification.objects.filter(created_at__lt=cut).delete()
    return deleted
