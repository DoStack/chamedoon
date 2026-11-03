"""Photos sent straight to storage: a signed URL, the browser's PUT, then a check here that resizes them like every other photo."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from django.db import transaction
from django.db.models import Min
from django.utils import timezone

from home.images import ImageError, process_image
from home.models import HomeListingEdit, HomeMedia, HomePost, HomeSource, HomeUpload, MediaStatus, UploadStatus
from home.storage import StorageError, get_storage
from home.submit import MAX_PHOTO_BYTES, MAX_PHOTOS

logger = logging.getLogger(__name__)

IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
PURGE_BATCH = 50


class UploadError(Exception):
    """`code` is the API's error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _storage():
    storage = get_storage()
    if storage is None:
        raise UploadError("upload_unavailable")
    return storage


def _live(user):
    cut = timezone.now() - timedelta(hours=HomeUpload.TTL_HOURS)
    return HomeUpload.objects.filter(user=user, created_at__gte=cut)


def start(user, content_type: str, size: int) -> tuple[HomeUpload, str, dict[str, str]]:
    if content_type not in IMAGE_TYPES:
        raise UploadError("not_image")
    if size > MAX_PHOTO_BYTES:
        raise UploadError("too_big")
    if _live(user).exclude(status=UploadStatus.POSTED).count() >= MAX_PHOTOS:
        raise UploadError("too_many")
    storage = _storage()
    pk = uuid.uuid4()
    raw_key = f"home/uploads/{pk}/raw"
    try:
        url, headers = storage.presign_put(raw_key, content_type, size)
    except StorageError as exc:
        logger.warning("upload_presign_failed: %s", exc)
        raise UploadError("upload_unavailable") from exc
    row = HomeUpload.objects.create(pk=pk, user=user, content_type=content_type, size=size, raw_key=raw_key)
    return row, url, headers


def _drop(storage, *keys: str) -> None:
    for key in keys:
        if not key:
            continue
        try:
            storage.delete(key)
        except StorageError as exc:
            logger.warning("upload_delete_failed: %s", exc)


def confirm(user, pk) -> HomeUpload:
    """Safe to retry: a checked photo answers again as it is."""
    row = _live(user).filter(pk=pk).first()
    if row is None:
        raise UploadError("photo_gone")
    if row.status != UploadStatus.PENDING:
        return row
    storage = _storage()
    try:
        data = storage.get(row.raw_key, limit=MAX_PHOTO_BYTES + 1)
    except StorageError as exc:
        logger.warning("upload_read_failed: %s", exc)
        raise UploadError("upload_unavailable") from exc
    if data is None:
        raise UploadError("upload_failed")
    if len(data) > MAX_PHOTO_BYTES:
        _drop(storage, row.raw_key)
        row.delete()
        raise UploadError("too_big")
    try:
        image = process_image(data)
    except ImageError as exc:
        _drop(storage, row.raw_key)
        row.delete()
        raise UploadError("not_image") from exc
    full_key = f"home/uploads/{row.pk}/{image.digest[:10]}-f.jpg"
    thumb_key = f"home/uploads/{row.pk}/{image.digest[:10]}-t.jpg"
    try:
        storage.put(full_key, image.full)
        storage.put(thumb_key, image.thumb)
    except StorageError as exc:
        logger.warning("upload_store_failed: %s", exc)
        raise UploadError("upload_unavailable") from exc
    row.full_key, row.thumb_key = full_key, thumb_key
    row.width, row.height, row.file_size, row.phash = image.width, image.height, len(image.full), image.phash
    row.status = UploadStatus.READY
    row.save()
    _drop(storage, row.raw_key)
    return row


def discard(user, pk) -> None:
    """A photo already on a post stays; removing what is not there is a success."""
    row = HomeUpload.objects.filter(user=user, pk=pk).exclude(status=UploadStatus.POSTED).first()
    if row is None:
        return
    storage = get_storage()
    if storage is not None:
        _drop(storage, row.raw_key, row.full_key, row.thumb_key)
    row.delete()


def payload(row: HomeUpload) -> dict:
    storage = _storage()
    return {"photo_id": row.pk, "url": storage.url(row.full_key), "thumb": storage.url(row.thumb_key)}


def usable(user, ids: list) -> list[HomeUpload]:
    """The rows for a post, in the order sent. One missing or unchecked photo stops the post, so nothing is dropped silently."""
    ids = [uuid.UUID(str(pk)) for pk in ids]
    rows = {row.pk: row for row in HomeUpload.objects.filter(user=user, pk__in=ids)}
    found = []
    for pk in ids:
        row = rows.get(pk)
        if row is None or row.status == UploadStatus.PENDING:
            raise UploadError("photo_gone")
        found.append(row)
    return found


def attach(post: HomePost, source: HomeSource, rows: list[HomeUpload]) -> int:
    """Makes the post's photos exactly these, in this order. Rows from this post keep their media; others join it."""
    current = {media.storage_key: media for media in HomeMedia.objects.filter(post=post, status=MediaStatus.STORED)}
    kept = set()
    # Unique per source, and never reused: a photo dropped and another added must not take its number.
    lowest = HomeMedia.objects.filter(post=post).aggregate(lowest=Min("telegram_message_id"))["lowest"]
    next_id = lowest - 1 if lowest is not None else -(post.pk * 100)
    for position, row in enumerate(rows):
        media = current.get(row.full_key)
        if media is None:
            media = HomeMedia.objects.create(
                source=source,
                post=post,
                telegram_message_id=next_id,
                position=position,
                status=MediaStatus.STORED,
                width=row.width,
                height=row.height,
                file_size=row.file_size,
                storage_key=row.full_key,
                thumb_key=row.thumb_key,
                phash=row.phash,
            )
            next_id -= 1
        elif media.position != position:
            media.position = position
            media.save(update_fields=["position"])
        kept.add(row.full_key)
        if row.status != UploadStatus.POSTED:
            row.status = UploadStatus.POSTED
            row.save(update_fields=["status"])
    removed = [media for key, media in current.items() if key not in kept]
    for media in removed:
        media.delete()
    HomeUpload.objects.filter(full_key__in=[media.storage_key for media in removed]).delete()
    if removed:
        # After commit: a rolled-back edit keeps its old photos, and no lock waits on storage.
        transaction.on_commit(lambda: _drop_media(removed))
    post.has_photo = bool(rows)
    post.photo_count = len(rows)
    post.save(update_fields=["has_photo", "photo_count"])
    return len(rows)


def _drop_media(removed: list[HomeMedia]) -> None:
    storage = get_storage()
    if storage is not None:
        for media in removed:
            _drop(storage, media.storage_key, media.thumb_key)


def for_post(user, post: HomePost) -> list[HomeUpload]:
    """The post's photos as uploads, so an edit can keep them by id. Photos from the old form get their row here."""
    rows = []
    known = {row.full_key: row for row in HomeUpload.objects.filter(user=user, status=UploadStatus.POSTED)}
    for media in HomeMedia.objects.filter(post=post, status=MediaStatus.STORED).order_by("position"):
        row = known.get(media.storage_key)
        if row is None:
            row = HomeUpload.objects.create(
                user=user,
                status=UploadStatus.POSTED,
                content_type="image/jpeg",
                size=media.file_size or 0,
                raw_key="",
                full_key=media.storage_key,
                thumb_key=media.thumb_key,
                width=media.width,
                height=media.height,
                file_size=media.file_size,
                phash=media.phash,
            )
        rows.append(row)
    return rows


def purge_abandoned(*, now=None) -> int:
    """Uploads never posted leave storage after a day, unless a held edit is still waiting on them."""
    cut = (now or timezone.now()) - timedelta(hours=HomeUpload.TTL_HOURS)
    held = {pk for ids in HomeListingEdit.objects.values_list("photo_ids", flat=True) for pk in ids}
    rows = list(HomeUpload.objects.filter(created_at__lt=cut).exclude(status=UploadStatus.POSTED).exclude(pk__in=held)[:PURGE_BATCH])
    storage = get_storage() if rows else None
    for row in rows:
        if storage is not None:
            _drop(storage, row.raw_key, row.full_key, row.thumb_key)
        row.delete()
    return len(rows)
