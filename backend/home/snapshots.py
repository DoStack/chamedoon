"""City snapshots: every public listing of a metro in one static file on R2, so browsing touches neither function nor DB.

`feeds/<city>.<version>.json` is immutable, and its version is a hash of its content, so writing the same content
twice uploads nothing. `feeds/manifest.json` has a short cache and names the current file of each city; it is written
every time, because its `generated_at` is how the client tells a stopped pipeline from a quiet one.

Files are stored gzip-9 with `Content-Encoding`, since R2 serves the bytes it holds. Only `public_card` fields go in.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import time
from datetime import timedelta

from django.db.models import Prefetch
from django.utils import timezone

from home import conf, deeplink
from home.card_filters import add_value_ratio
from home.feed import FEED_DAYS
from home.models import HomeChannel, HomeListing, HomeMedia, ListingKind, ListingStatus, MediaStatus, PostStatus
from home.public import public_card
from home.publish import city_channels, route
from home.storage import IMMUTABLE_CACHE, StorageError, get_storage

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MANIFEST_KEY = "feeds/manifest.json"
MANIFEST_CACHE = "public, max-age=60"
JSON_TYPE = "application/json; charset=utf-8"
# A client can hold a manifest for a minute and a list for longer, so a few old versions stay readable.
KEEP_VERSIONS = 3
BUDGET_BYTES = 300 * 1024
PER_CITY_RESERVE_SECONDS = 5


def manifest_url() -> str:
    """The public address of the manifest, or blank when snapshots are off or storage is not set up."""
    if not conf.row().snapshots_on:
        return ""
    storage = get_storage()
    return storage.url(MANIFEST_KEY) if storage is not None else ""


def compress(body: bytes) -> bytes:
    """gzip-9 with no timestamp, so the same content always gives the same bytes."""
    return gzip.compress(body, compresslevel=9, mtime=0)


def _dump(data: object) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _shown_listings(country: str | None = None):
    """Listings people may see: active, trusted, seen this month. The same rule as the feed's base_queryset."""
    rows = HomeListing.objects.filter(
        status=ListingStatus.ACTIVE,
        needs_review=False,
        post__status=PostStatus.ACCEPTED,
        last_seen__gte=timezone.now() - timedelta(days=FEED_DAYS),
    )
    if country:
        rows = rows.filter(country=country)
    return rows.select_related("post").prefetch_related(
        Prefetch(
            "post__media",
            queryset=HomeMedia.objects.filter(status=MediaStatus.STORED).order_by("position"),
            to_attr="stored_media",
        )
    )


def city_cards(channel: HomeChannel, channels: list[HomeChannel] | None = None) -> list[dict]:
    """The cards of one metro: every shown listing `route()` sends to this channel, so it matches posts and the gate."""
    channels = city_channels() if channels is None else channels
    cards = [public_card(listing, channels) for listing in _shown_listings(channel.country) if route(listing, channels) == channel]
    add_value_ratio(cards)
    return sorted(cards, key=lambda card: card["id"])


def _file_key(slug: str, version: str) -> str:
    return f"feeds/{slug}.{version}.json"


def _write_city(storage, channel: HomeChannel, channels: list[HomeChannel], saved: dict, now_text: str) -> dict:
    """Write one city's file if its content changed. Returns its entry in the saved state."""
    slug = deeplink.slug(channel.city)
    cards = city_cards(channel, channels)
    version = hashlib.sha256(_dump({"schema_version": SCHEMA_VERSION, "listings": cards})).hexdigest()[:12]
    entry = saved.get(slug) or {}
    if entry.get("version") == version:
        return entry
    key = _file_key(slug, version)
    body = compress(
        _dump(
            {
                "schema_version": SCHEMA_VERSION,
                "country": channel.country,
                "city": channel.city,
                "city_slug": slug,
                "version": version,
                "generated_at": now_text,
                "listings": cards,
            }
        )
    )
    if len(body) > BUDGET_BYTES:
        logger.warning("snapshot_over_budget city=%s bytes=%d listings=%d", slug, len(body), len(cards))
    storage.put(key, body, JSON_TYPE, content_encoding="gzip", cache_control=IMMUTABLE_CACHE)
    keys = [key, *[old for old in entry.get("keys", []) if old != key]]
    for old in keys[KEEP_VERSIONS:]:
        try:
            storage.delete(old)
        except StorageError as exc:
            logger.warning("snapshot_prune_failed key=%s error=%s", old, str(exc)[:120])
    return {"version": version, "keys": keys[:KEEP_VERSIONS], "bytes": len(body), "listing_count": len(cards)}


def _manifest(storage, channels: list[HomeChannel], state: dict, row, now_text: str) -> dict:
    cities = []
    for channel in channels:
        slug = deeplink.slug(channel.city)
        entry = state.get(slug)
        if entry:
            cities.append(
                {
                    "country": channel.country,
                    "city": channel.city,
                    "city_slug": slug,
                    "version": entry["version"],
                    "url": storage.url(_file_key(slug, entry["version"])),
                    "listing_count": entry["listing_count"],
                    "bytes": entry["bytes"],
                }
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_text,
        "alerts_on": row.alerts_enabled,
        "maintenance": {
            "on": row.maintenance_on,
            "until": row.maintenance_until.isoformat() if row.maintenance_until else None,
        },
        "cities": cities,
    }


def write_snapshots(*, only: list[HomeChannel] | None = None, stop_at: float | None = None) -> dict:
    """Write what changed and the manifest. Does nothing while `snapshots_on` is off. Never raises: posting must go on."""
    row = conf.row()
    if not row.snapshots_on:
        return {"ok": True, "skipped": "snapshots are off in Home settings"}
    storage = get_storage()
    if storage is None:
        return {"ok": True, "skipped": "photo storage is not set up"}
    out = {"ok": True, "cities": 0, "written": 0, "unchanged": 0, "failed": 0}
    channels = city_channels()
    state = dict(row.snapshot_state or {})
    now_text = timezone.now().isoformat()
    problem = ""
    for channel in channels if only is None else only:
        if stop_at is not None and time.monotonic() + PER_CITY_RESERVE_SECONDS >= stop_at:
            break
        slug = deeplink.slug(channel.city)
        before = (state.get(slug) or {}).get("version")
        try:
            state[slug] = _write_city(storage, channel, channels, state, now_text)
        except StorageError as exc:
            problem = f"{slug}: {exc}"[:300]
            out["failed"] += 1
            logger.warning("snapshot_failed city=%s error=%s", slug, str(exc)[:200])
            continue
        out["cities"] += 1
        out["written" if state[slug]["version"] != before else "unchanged"] += 1
    update = {"snapshot_state": state, "snapshot_error": problem}
    try:
        manifest = _dump(_manifest(storage, channels, state, row, now_text))
        storage.put(MANIFEST_KEY, compress(manifest), JSON_TYPE, content_encoding="gzip", cache_control=MANIFEST_CACHE)
        if not problem:
            update["snapshots_written_at"] = timezone.now()
    except StorageError as exc:
        update["snapshot_error"] = f"manifest: {exc}"[:300]
        logger.warning("snapshot_manifest_failed error=%s", str(exc)[:200])
        out["failed"] += 1
    type(row).objects.filter(pk=row.pk).update(**update)
    out["ok"] = out["failed"] == 0
    return out


def after_submit(listing: HomeListing) -> None:
    """A person just posted: their city's file changes now, not at the next run. Never fails the post."""
    try:
        if not conf.row().snapshots_on or listing.kind not in ListingKind.values or listing.needs_review:
            return
        channel = route(listing, city_channels())
        if channel is not None:
            write_snapshots(only=[channel])
    except Exception:  # noqa: BLE001 - the post is saved; the next run writes the file
        logger.exception("snapshot_after_submit_failed listing=%s", listing.pk)
