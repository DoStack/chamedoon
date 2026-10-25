"""Listings that people post themselves from the Mini App.

They go through the same model and checks as listings read from Telegram: each one
gets a HomePost under a per-country "app" source (never read from Telegram), and the
form values pass through normalize_listing, so an odd price lands in the review
queue exactly like an AI-extracted one. Photos are resized in the browser, then
shrunk, hashed and stored like Telegram photos.
"""

from __future__ import annotations

import logging
import time

from django.db import transaction
from django.utils import timezone

from home.images import ImageError, process_image
from home.models import (
    HomeListing,
    HomeMedia,
    HomePost,
    HomeSource,
    ListingKind,
    ListingStatus,
    MediaStatus,
    PostStatus,
    PropertyType,
    RentalMode,
)
from home.review import link_duplicate, normalize_listing, text_key
from home.storage import StorageError, get_storage, media_key

logger = logging.getLogger(__name__)

APP_PREFIX = "app_"
CURRENCY_BY_COUNTRY = {"DE": "EUR", "CA": "CAD"}
MAX_PHOTOS = 6
MAX_PHOTO_BYTES = 3 * 1024 * 1024
DESCRIPTION_MIN = 20
DESCRIPTION_MAX = 1500


class SubmitError(ValueError):
    """A form problem. `code` is the message key shown to the person."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def app_source(country: str) -> HomeSource:
    source, _ = HomeSource.objects.get_or_create(
        username=f"{APP_PREFIX}{country.lower()}",
        defaults={
            "title": "Chamedoon Home app",
            "kind": "group",
            "country": country,
            "default_currency": CURRENCY_BY_COUNTRY.get(country, "EUR"),
            "enabled": False,
            "fetch_photos": False,
        },
    )
    return source


def _number(value) -> str:
    raw = str(value or "").strip().replace(",", "")
    return raw if raw.replace(".", "", 1).isdigit() else ""


def create_listing(user, form: dict, photos: list[bytes], *, locale: str, countries: list[str]) -> HomeListing:
    country = str(form.get("country") or "").upper()
    if country not in countries:
        raise SubmitError("country")
    kind = form.get("kind") if form.get("kind") in {ListingKind.OFFER, ListingKind.WANTED} else ""
    if not kind:
        raise SubmitError("kind")
    property_type = form.get("property_type") if form.get("property_type") in {c.value for c in PropertyType} else ""
    if not property_type:
        raise SubmitError("type")
    city = str(form.get("city") or "").strip()[:64]
    if not city:
        raise SubmitError("city")
    description = str(form.get("description") or "").strip()
    if len(description) < DESCRIPTION_MIN:
        raise SubmitError("description")
    description = description[:DESCRIPTION_MAX]
    price = _number(form.get("price"))
    if kind == ListingKind.OFFER and not price:
        raise SubmitError("price")
    phone = str(form.get("phone") or "").strip()[:24]
    username = (user.telegram_username or "").strip().lstrip("@")
    if not username and not phone:
        raise SubmitError("contact")

    source = app_source(country)
    now = timezone.now()
    summary = description[:500]
    data = {
        "accept": True,
        "kind": kind,
        "property_type": property_type,
        "rental_mode": form.get("rental_mode") if form.get("rental_mode") in {c.value for c in RentalMode} else RentalMode.UNKNOWN,
        "country": country,
        "city": city,
        "area": str(form.get("area") or "").strip()[:96],
        "price_amount": price if kind == ListingKind.OFFER else None,
        "price_max": price if kind == ListingKind.WANTED else None,
        "price_currency": CURRENCY_BY_COUNTRY.get(country, ""),
        "price_period": form.get("price_period") or "month",
        "bills_included": form.get("bills_included") or "unknown",
        "deposit_amount": _number(form.get("deposit")) or None,
        "size_m2": _number(form.get("size_m2")) or None,
        "rooms": _number(form.get("rooms")) or None,
        "furnished": form.get("furnished") or "unknown",
        "registration": form.get("registration") or "unknown",
        "available_from": form.get("available_from") or None,
        "min_term_months": _number(form.get("min_term_months")) or None,
        "roommate_gender": form.get("roommate_gender") or "any",
        "lister_type": "owner",
        "contact_telegram": username,
        "contact_phone": phone,
        "summary_fa": summary if locale == "fa" else "",
        "summary_en": summary if locale != "fa" else "",
        "confidence": {"overall": 1.0},
    }
    with transaction.atomic():
        post = HomePost.objects.create(
            source=source,
            telegram_message_id=-int(time.time() * 1000),
            posted_at=now,
            text=description,
            author_username=username[:64],
            author_name=(user.first_name or "")[:128],
            author_id=user.telegram_user_id,
            text_key=text_key(description),
            status=PostStatus.ACCEPTED,
            review_json=data,
            model="app",
            reviewed_at=now,
        )
        fields, notes = normalize_listing(post, data)
        if fields is None:
            raise SubmitError("city")
        listing = HomeListing.objects.create(
            post=post,
            **fields,
            needs_review=bool(notes),
            review_note="; ".join(notes)[:255],
            first_seen=now,
            last_seen=now,
        )
    stored = _store_photos(post, source, photos)
    if stored:
        post.has_photo = True
        post.photo_count = stored
        post.save(update_fields=["has_photo", "photo_count"])
    link_duplicate(listing)
    listing.refresh_from_db()
    return listing


def _store_photos(post: HomePost, source: HomeSource, photos: list[bytes]) -> int:
    storage = get_storage()
    if storage is None:
        return 0
    stored = 0
    for index, data in enumerate(photos[:MAX_PHOTOS]):
        if not data or len(data) > MAX_PHOTO_BYTES:
            continue
        try:
            image = process_image(data)
        except ImageError:
            continue
        media = HomeMedia.objects.create(
            source=source,
            post=post,
            telegram_message_id=-(post.pk * 100 + index),
            position=stored,
        )
        full_key = media_key(source.country, media.pk, image.digest)
        thumb_key = media_key(source.country, media.pk, image.digest, thumb=True)
        try:
            storage.put(full_key, image.full)
            storage.put(thumb_key, image.thumb)
        except StorageError as exc:
            logger.warning("App photo upload failed: %s", exc)
            media.status = MediaStatus.FAILED
            media.error = str(exc)[:200]
            media.save(update_fields=["status", "error"])
            continue
        media.status = MediaStatus.STORED
        media.width, media.height = image.width, image.height
        media.file_size = len(image.full)
        media.storage_key, media.thumb_key = full_key, thumb_key
        media.phash = image.phash
        media.save(update_fields=["status", "width", "height", "file_size", "storage_key", "thumb_key", "phash"])
        stored += 1
    return stored


def user_listings(user):
    return (
        HomeListing.objects.filter(post__author_id=user.telegram_user_id, post__source__username__startswith=APP_PREFIX)
        .select_related("post")
        .order_by("-first_seen")
    )


def close_listing(user, pk: int) -> bool:
    listing = user_listings(user).filter(pk=pk, status=ListingStatus.ACTIVE).first()
    if listing is None:
        return False
    listing.status = ListingStatus.EXPIRED
    listing.save(update_fields=["status", "updated_at"])
    return True
