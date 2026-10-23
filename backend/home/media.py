"""Download, shrink, hash and store the photos of accepted listings.

Only photos of listings that passed the AI review and are not reposts are fetched, so
Toronto's heavy reposting costs no storage. A listing's album is finished together,
then compared by photo hash with other listings in the same city.
"""

from __future__ import annotations

import logging
import time

from home.images import ImageError, process_image
from home.models import HomeListing, HomeMedia, ListingStatus, MediaStatus, PostStatus
from home.review import link_photo_duplicates
from home.storage import StorageError, get_storage, media_key

logger = logging.getLogger(__name__)

MAX_PHOTOS_PER_POST = 10
PER_PHOTO_RESERVE_SECONDS = 6


def pending_media():
    return (
        HomeMedia.objects.filter(
            status=MediaStatus.PENDING,
            post__status=PostStatus.ACCEPTED,
            post__listing__status=ListingStatus.ACTIVE,
            source__fetch_photos=True,
        )
        .select_related("post", "post__listing", "source")
        .order_by("-post__posted_at", "post_id", "position")
    )


def storage_skip_message() -> str:
    """Why photos are not stored: switched off (no backend), or the backend's settings are missing."""
    from api.config_check import MISSING, photo_storage, report_missing

    feature = photo_storage()
    if feature.state == MISSING and feature.missing:
        return "photo storage is not configured: " + report_missing("housing photos", feature.missing)
    return f"photo storage is not configured ({feature.note or 'HOME_MEDIA_BACKEND'})"


def process_pending_media(reader, *, limit: int = 20, stop_at: float | None = None) -> dict:
    storage = get_storage()
    if storage is None:
        return {"ok": True, "skipped": storage_skip_message(), "stored": 0, "failed": 0}
    stored = failed = 0
    touched: dict[int, HomeListing] = {}
    for media in pending_media()[: limit * 2]:
        if stored + failed >= limit:
            break
        if stop_at is not None and time.monotonic() + PER_PHOTO_RESERVE_SECONDS >= stop_at:
            break
        if media.position >= MAX_PHOTOS_PER_POST:
            media.status = MediaStatus.SKIPPED
            media.error = "album limit"
            media.save(update_fields=["status", "error"])
            continue
        try:
            data = reader.download_photo(media.source.username, media.telegram_message_id)
            if not data:
                raise ImageError("photo is gone or not a photo")
            image = process_image(data)
            country = media.source.country
            full_key = media_key(country, media.pk, image.digest)
            thumb_key = media_key(country, media.pk, image.digest, thumb=True)
            storage.put(full_key, image.full)
            storage.put(thumb_key, image.thumb)
        except (ImageError, StorageError) as exc:
            _fail(media, str(exc))
            failed += 1
            continue
        except Exception as exc:  # noqa: BLE001 - a Telegram error must not stop the batch
            logger.warning("Photo %s/%s failed: %s", media.source.username, media.telegram_message_id, exc)
            _fail(media, f"{type(exc).__name__}: {exc}")
            failed += 1
            continue
        media.status = MediaStatus.STORED
        media.width, media.height = image.width, image.height
        media.file_size = len(image.full)
        media.storage_key, media.thumb_key = full_key, thumb_key
        media.phash = image.phash
        media.error = ""
        media.save(update_fields=["status", "width", "height", "file_size", "storage_key", "thumb_key", "phash", "error"])
        stored += 1
        touched[media.post_id] = media.post.listing
    merged = 0
    for listing in touched.values():
        if not HomeMedia.objects.filter(post_id=listing.post_id, status=MediaStatus.PENDING).exists():
            listing.refresh_from_db()
            if link_photo_duplicates(listing) is not None:
                merged += 1
    return {
        "ok": True,
        "stored": stored,
        "failed": failed,
        "merged_by_photo": merged,
        "remaining": pending_media().count(),
    }


def _fail(media: HomeMedia, error: str) -> None:
    media.status = MediaStatus.FAILED
    media.error = error[:200]
    media.save(update_fields=["status", "error"])
