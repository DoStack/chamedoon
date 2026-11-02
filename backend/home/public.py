"""What anyone may see of a listing: no contact, no source link, no source group.

Every public surface of the new Mini App (API detail, fallback feed, city snapshots) builds on these functions.
"""

from __future__ import annotations

from decimal import Decimal

from home import deeplink
from home.alerts import monthly_price
from home.contact_text import has_contact, redact_contact
from home.feed import photos_for
from home.freshness import reporters
from home.models import (
    HomeChannel,
    HomeListing,
    ListingStatus,
    PostStatus,
    ReportReason,
)
from home.publish import city_channels, route

ACTIVE = "active"
CLOSED = "closed"
GONE = "gone"
_FOLLOW_LIMIT = 3


def visible_listing(pk: int) -> tuple[HomeListing | None, str]:
    """The listing a link to `pk` should show, and its state: active, closed or gone.

    A duplicate shows the listing it was merged into. Hidden rows (reported, or waiting for review) are gone.
    """
    listing = HomeListing.objects.select_related("post", "canonical").filter(pk=pk).first()
    for _ in range(_FOLLOW_LIMIT):
        if listing is None or listing.status != ListingStatus.DUPLICATE:
            break
        listing = listing.canonical
    if listing is None:
        return None, GONE
    return listing, listing_state(listing)


def listing_state(listing: HomeListing) -> str:
    if listing.status in {ListingStatus.REPORTED, ListingStatus.DUPLICATE} or listing.needs_review:
        return GONE
    if listing.post.status != PostStatus.ACCEPTED and not listing.published_at:
        return GONE
    if listing.status in {ListingStatus.EXPIRED, ListingStatus.RENTED}:
        return CLOSED
    return ACTIVE


def closed_reason(listing: HomeListing) -> str | None:
    """Who closed it: people who marked it taken, the owner, or nobody (not seen for a while)."""
    if listing.status == ListingStatus.RENTED:
        return "users" if reporters(listing, ReportReason.TAKEN) else "owner"
    if listing.status == ListingStatus.EXPIRED:
        return "stale"
    return None


def has_source(listing: HomeListing) -> bool:
    """A source post the gate can open. Private group links (/c/) never open for a stranger."""
    source_url = listing.post.source_url
    return bool(source_url) and "/c/" not in source_url


def gate_channel(listing: HomeListing, channels: list[HomeChannel] | None = None) -> HomeChannel | None:
    """The channel a listing's contact sits behind: its city channel, else its country channel, else none."""
    city_channel = route(listing, city_channels() if channels is None else channels)
    if city_channel is not None:
        return city_channel
    return HomeChannel.objects.filter(enabled=True, country=listing.country, city="").first()


def known_handles(listing: HomeListing) -> tuple[str, ...]:
    return (listing.contact_telegram, listing.post.author_username)


def clean_place(text: str, known: tuple[str, ...]) -> str:
    """A place name goes out whole or not at all: what is left after cutting a contact out of it is not a place."""
    return "" if has_contact(text, known) else text


def _number(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def public_card(listing: HomeListing, channels: list[HomeChannel] | None = None) -> dict:
    """The fields of one card. Filters and counts in the client read only these."""
    metro = route(listing, city_channels() if channels is None else channels)
    photos = photos_for(listing)
    known = known_handles(listing)
    return {
        "id": listing.pk,
        "kind": listing.kind,
        "property_type": listing.property_type,
        "rental_mode": listing.rental_mode,
        "country": listing.country,
        "region": listing.region,
        "city": clean_place(listing.city, known),
        "metro": metro.city if metro else "",
        "metro_slug": deeplink.slug(metro.city) if metro else "",
        "area": clean_place(listing.area, known),
        "price_amount": _number(listing.price_amount),
        "price_max": _number(listing.price_max),
        "price_currency": listing.price_currency,
        "price_period": listing.price_period,
        "price_basis": listing.price_basis,
        "price_monthly": _number(monthly_price(listing)),
        "rooms": _number(listing.rooms),
        "size_m2": _number(listing.size_m2),
        "furnished": listing.furnished,
        "bills_included": listing.bills_included,
        "registration": listing.registration,
        "roommate_gender": listing.roommate_gender,
        "lister_type": listing.lister_type,
        "available_from": listing.available_from.isoformat() if listing.available_from else None,
        "available_to": listing.available_to.isoformat() if listing.available_to else None,
        "first_seen": listing.first_seen.isoformat(),
        "last_seen": listing.last_seen.isoformat(),
        "thumb": photos[0]["thumb"] if photos else "",
        "photo_count": len(photos),
    }


def public_listing(listing: HomeListing, channels: list[HomeChannel] | None = None) -> dict:
    """The fields of the detail screen: the card, the rest of the facts, and what the gate will offer."""
    channel = gate_channel(listing, channels)
    state = listing_state(listing)
    flags = listing.flags if isinstance(listing.flags, dict) else {}
    known = known_handles(listing)
    return {
        **public_card(listing, channels),
        "state": state,
        "closed_reason": closed_reason(listing) if state == CLOSED else None,
        "deposit_amount": _number(listing.deposit_amount),
        "deposit_months": _number(listing.deposit_months),
        "min_term_months": listing.min_term_months,
        "occupants_wanted": listing.occupants_wanted,
        "wbs_required": bool(flags.get("wgb")),
        "summary_fa": redact_contact(listing.summary_fa, known),
        "summary_en": redact_contact(listing.summary_en, known),
        "last_confirmed_at": listing.last_confirmed_at.isoformat() if listing.last_confirmed_at else None,
        "photos": [{"url": photo["full"], "thumb": photo["thumb"]} for photo in photos_for(listing)],
        "has_telegram": bool(listing.contact_telegram),
        "has_phone": bool(listing.contact_phone),
        "has_source": has_source(listing),
        "channel": {"username": channel.username, "title": channel.title, "city": channel.city} if channel else None,
    }
