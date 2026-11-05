"""A photo sent in an outreach chat joins the sender's own ad, the way a photo from the posting form does."""

from __future__ import annotations

import base64
import binascii
import logging
import time

from django.db import transaction
from django.db.models import Max, Min

from home.images import ImageError, process_image
from home.models import HomeListing, HomeMedia, HomeSource, ListingKind, ListingStatus, MediaStatus
from home.ownership import owned_listings
from home.storage import StorageError, get_storage
from home.submit import MAX_PHOTO_BYTES, MAX_PHOTOS
from outreach.models import OutreachTurn, TurnDirection

logger = logging.getLogger(__name__)


def _target(turn: OutreachTurn) -> HomeListing | None:
    """Their only live offer. With several, nothing is guessed."""
    offers = list(owned_listings(turn.conversation.telegram_user_id).filter(kind=ListingKind.OFFER, status=ListingStatus.ACTIVE)[:2])
    return offers[0] if len(offers) == 1 else None


def attach_chat_photo(turn_id: int, data_base64: str) -> dict:
    turn = OutreachTurn.objects.select_related("conversation").filter(pk=turn_id, direction=TurnDirection.IN, has_photo=True).first()
    if turn is None:
        return {"ok": False, "error": "unknown_turn"}
    if turn.photo_key:
        return {"ok": True, "listing": (turn.action_detail or {}).get("photo_listing")}
    listing = _target(turn)
    if listing is None:
        return {"ok": False, "error": "no_single_listing"}
    try:
        data = base64.b64decode(data_base64, validate=True)
    except (binascii.Error, ValueError):
        return {"ok": False, "error": "bad_data"}
    if not data or len(data) > MAX_PHOTO_BYTES:
        return {"ok": False, "error": "too_big"}
    storage = get_storage()
    if storage is None:
        return {"ok": False, "error": "no_storage"}
    try:
        image = process_image(data)
    except ImageError:
        return {"ok": False, "error": "not_image"}
    full_key = f"home/chat/{turn.pk}/{image.digest[:10]}-f.jpg"
    thumb_key = f"home/chat/{turn.pk}/{image.digest[:10]}-t.jpg"
    try:
        storage.put(full_key, image.full)
        storage.put(thumb_key, image.thumb)
    except StorageError as exc:
        logger.warning("chat_photo_store_failed: %s", exc)
        return {"ok": False, "error": "storage"}
    post = listing.post
    with transaction.atomic():
        if HomeMedia.objects.filter(post=post, status=MediaStatus.STORED).count() >= MAX_PHOTOS:
            return {"ok": False, "error": "too_many"}
        # Same numbering as the posting form: unique per source, below every real message id.
        HomeSource.objects.select_for_update().filter(pk=post.source_id).first()
        lowest = HomeMedia.objects.filter(source_id=post.source_id).aggregate(lowest=Min("telegram_message_id"))["lowest"]
        position = (HomeMedia.objects.filter(post=post).aggregate(top=Max("position"))["top"] or -1) + 1
        HomeMedia.objects.create(
            source_id=post.source_id,
            post=post,
            telegram_message_id=min(lowest or 0, -int(time.time() * 1000)) - 1,
            position=position,
            status=MediaStatus.STORED,
            width=image.width,
            height=image.height,
            file_size=len(image.full),
            storage_key=full_key,
            thumb_key=thumb_key,
            phash=image.phash,
        )
        post.has_photo = True
        post.photo_count = HomeMedia.objects.filter(post=post, status=MediaStatus.STORED).count()
        post.save(update_fields=["has_photo", "photo_count"])
        turn.photo_key = full_key
        turn.action_detail = {**(turn.action_detail or {}), "photo_listing": listing.pk}
        turn.save(update_fields=["photo_key", "action_detail"])
    from home.snapshots import after_submit

    after_submit(listing)
    return {"ok": True, "listing": listing.pk}
