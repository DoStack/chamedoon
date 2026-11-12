"""Listings of one Telegram username, and photos the admin sends for one of them."""

from __future__ import annotations

import logging
import re
import time

from django.db import transaction
from django.db.models import Min, Q

from home import conf as home_conf
from home.images import ImageError, process_image
from home.models import HomeListing, HomeMedia, HomePost, HomeSource, MediaStatus
from home.storage import StorageError, get_storage, media_key

logger = logging.getLogger("adminyar")

MAX_LISTINGS = 10
MAX_PHOTOS = 10
MAX_PHOTO_BYTES = 10 * 1024 * 1024
_USERNAME = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{3,31})$")
_LINK = re.compile(r"(?:https?://)?t\.me/(?:c/)?([A-Za-z0-9_]+)/(\d+)", re.I)


def clean_username(text: str) -> str | None:
    text = (text or "").strip()
    text = re.sub(r"^(?:https?://)?t\.me/", "", text, flags=re.I)
    found = _USERNAME.match(text)
    return found.group(1) if found else None


def for_username(username: str) -> list[HomeListing]:
    """Live listings whose contact or post author is this username, newest first."""
    name = username.lstrip("@")
    query = Q(contact_telegram__iexact=name) | Q(contact_telegram__iexact=f"@{name}") | Q(post__author_username__iexact=name)
    return list(
        HomeListing.objects.filter(query, canonical__isnull=True)
        .select_related("post")
        .order_by("-last_seen", "-id")[:MAX_LISTINGS]
    )


def from_post_link(text: str) -> HomeListing | None:
    found = _LINK.search(text or "")
    if not found:
        return None
    source, message_id = found.group(1), int(found.group(2))
    post = HomePost.objects.filter(source__username__iexact=source, telegram_message_id=message_id).first()
    return getattr(post, "listing", None) if post else None


def title(listing: HomeListing) -> str:
    return (listing.summary_fa or listing.summary_en or f"{listing.get_property_type_display()}").strip()[:60]


def price(listing: HomeListing) -> str:
    if listing.price_amount is None:
        return "بدون قیمت"
    amount = f"{int(listing.price_amount):,}"
    return f"{amount} {listing.price_currency}".strip()


def link(listing: HomeListing) -> str:
    return home_conf.mini_app_link(listing.pk)


def store_photo(listing: HomeListing, data: bytes) -> str:
    """Adds one photo to the end of the listing's post. Returns 'ok', 'bad_image', 'too_big', 'full', 'no_storage' or 'failed'."""
    if not data or len(data) > MAX_PHOTO_BYTES:
        return "too_big"
    post = listing.post
    if HomeMedia.objects.filter(post=post, status=MediaStatus.STORED).count() >= MAX_PHOTOS:
        return "full"
    storage = get_storage()
    if storage is None:
        return "no_storage"
    try:
        image = process_image(data)
    except ImageError:
        return "bad_image"
    source = post.source
    with transaction.atomic():
        HomeSource.objects.select_for_update().filter(pk=source.pk).first()
        lowest = HomeMedia.objects.filter(source=source).aggregate(lowest=Min("telegram_message_id"))["lowest"]
        next_id = min(lowest or 0, -int(time.time() * 1000)) - 1
        position = HomeMedia.objects.filter(post=post, status=MediaStatus.STORED).count()
        media = HomeMedia.objects.create(source=source, post=post, telegram_message_id=next_id, position=position)
    full_key = media_key(source.country, media.pk, image.digest)
    thumb_key = media_key(source.country, media.pk, image.digest, thumb=True)
    try:
        storage.put(full_key, image.full)
        storage.put(thumb_key, image.thumb)
    except StorageError as exc:
        logger.warning("Adminyar photo upload failed: %s", exc)
        media.status, media.error = MediaStatus.FAILED, str(exc)[:200]
        media.save(update_fields=["status", "error"])
        return "failed"
    media.status = MediaStatus.STORED
    media.width, media.height, media.file_size = image.width, image.height, len(image.full)
    media.storage_key, media.thumb_key, media.phash = full_key, thumb_key, image.phash
    media.save(update_fields=["status", "width", "height", "file_size", "storage_key", "thumb_key", "phash"])
    post.has_photo = True
    post.photo_count = HomeMedia.objects.filter(post=post, status=MediaStatus.STORED).count()
    post.save(update_fields=["has_photo", "photo_count"])
    return "ok"
