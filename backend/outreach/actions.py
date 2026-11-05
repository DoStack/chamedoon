"""The closed list of things the agent may do, each on one listing the caller has already checked is theirs.

Extend, rented, remove, fill a field, add a photo, put the phone on the ad. Nothing else changes a listing.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from home import freshness, status_posts
from home.models import HomeListing, HomeListingEdit, ListingKind, ListingStatus, StatusPost

PHONE = re.compile(r"^\+?\d{7,15}$")
MAX_PRICE = Decimal("20000")


def extend_listing(listing: HomeListing, telegram_id: int) -> None:
    """«هنوز هست»: an expired ad comes back, an active one counts as confirmed by its owner."""
    now = timezone.now()
    if listing.status == ListingStatus.EXPIRED:
        listing.status = ListingStatus.ACTIVE
        listing.last_seen = now
        listing.last_confirmed_at = now
        listing.save(update_fields=["status", "last_seen", "last_confirmed_at", "updated_at"])
        if listing.status_post:
            status_posts.apply(listing, StatusPost.RESTORED)
        return
    freshness.confirm_available(listing, telegram_id)


def mark_rented(listing: HomeListing) -> bool:
    return freshness.set_rented(listing)


def remove_listing(listing: HomeListing) -> None:
    """Gone for everyone, its channel posts edited to closed, its contact dropped. Same as the owner's delete."""
    with transaction.atomic():
        current = HomeListing.objects.select_for_update(of=("self",)).select_related("post").get(pk=listing.pk)
        if current.status == ListingStatus.REMOVED:
            return
        was_open = current.status == ListingStatus.ACTIVE
        current.status = ListingStatus.REMOVED
        current.dedup_key = ""
        current.contact_telegram = ""
        current.contact_phone = ""
        current.save(update_fields=["status", "dedup_key", "contact_telegram", "contact_phone", "updated_at"])
        HomeListingEdit.objects.filter(listing=current).delete()
    if was_open:
        from home.inbox import listing_closed

        listing_closed(current)
    if current.published_at or current.publications.exists():
        status_posts.apply(current, StatusPost.RENTED)


def _decimal(value: str) -> Decimal | None:
    text = (value or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٫,", "0123456789.."))
    found = re.search(r"\d+(?:\.\d+)?", text)
    if not found:
        return None
    try:
        return Decimal(found.group(0))
    except InvalidOperation:
        return None


def fill_field(listing: HomeListing, field: str, value: str) -> bool:
    """H4: one fact from the chat. Only price, rooms and neighbourhood, only sensible values."""
    if listing.status != ListingStatus.ACTIVE or listing.kind != ListingKind.OFFER:
        return False
    updates: dict = {}
    if field == "price":
        number = _decimal(value)
        if number is None or not Decimal("50") <= number <= MAX_PRICE:
            return False
        updates["price_amount"] = number
    elif field == "rooms":
        number = _decimal(value)
        if number is None or not Decimal("0.5") <= number <= Decimal("12"):
            return False
        updates["rooms"] = number
    elif field == "area":
        text = re.sub(r"\s+", " ", value or "").strip()[:96]
        if len(text) < 2 or "@" in text or "http" in text or re.search(r"\d{6,}", text):
            return False
        updates["area"] = text
    else:
        return False
    HomeListing.objects.filter(pk=listing.pk).update(**updates, updated_at=timezone.now())
    listing.refresh_from_db()
    _after_change(listing, area=field == "area")
    return True


def set_phone(listing: HomeListing, phone: str) -> bool:
    """H6: only after an explicit yes, because the number becomes public on the ad."""
    phone = re.sub(r"[^\d+]", "", phone or "")
    if not PHONE.fullmatch(phone) or listing.status != ListingStatus.ACTIVE:
        return False
    HomeListing.objects.filter(pk=listing.pk).update(contact_phone=phone[:24], updated_at=timezone.now())
    listing.refresh_from_db()
    _after_change(listing)
    return True


def _after_change(listing: HomeListing, *, area: bool = False) -> None:
    from home.snapshots import after_submit

    if area:
        from home.areas import link_area

        link_area(listing)
    after_submit(listing)
    if listing.published_at or listing.publications.exists():
        status_posts.apply(listing, StatusPost.RESTORED)
