"""City snapshots: every public listing of a metro in one static file on R2, so browsing touches neither function nor DB.

The app reads them at /feeds/ on its own origin: `rewrites` in vercel.json forwards that path to the bucket's public
address, which sends no CORS header. That address must match `HomeSettings.r2_public_base`.

`feeds/<cc>-<city>.<version>.json` never changes once written, and its version is a hash of its content, so writing
the same content twice uploads nothing. `feeds/manifest.json` has a short cache and names the current file of each
city. Its `generated_at` is the last whole run, which is how the client tells a stopped pipeline from a quiet one.

Order matters: new files first, then the manifest, and only after the manifest went out is the state saved and are old
files deleted. A client can never be pointed at a file that does not exist.

Files are stored gzip-9 with `Content-Encoding`, since R2 serves the bytes it holds. Only `public_card` fields go in.

Taking a contact down: fix the listing, run the writer so the manifest stops naming the old file, then run
`manage.py home_snapshots_purge` to delete old files from R2. CDN copies last at most a day (`FILE_CACHE`); purge the
Vercel cache from the project's settings to end them sooner.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import time
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Prefetch, Q
from django.utils import timezone

from home import conf, deeplink
from home.card_filters import add_value_ratio
from home.feed import FEED_DAYS
from home.models import HomeChannel, HomeListing, HomeMedia, ListingKind, ListingStatus, MediaStatus, PostStatus
from home.public import public_card
from home.publish import city_channels, route
from home.storage import PUT_TIMEOUT_SECONDS, StorageError, get_storage

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MANIFEST_KEY = "feeds/manifest.json"
MANIFEST_CACHE = "public, max-age=60"
# Not a year: a contact taken out of a listing must not stay public on the edges for long.
FILE_CACHE = "public, max-age=86400"
JSON_TYPE = "application/json; charset=utf-8"
# A client can hold a manifest for a minute and a list for longer, so a few old versions stay readable.
KEEP_VERSIONS = 3
BUDGET_BYTES = 300 * 1024
MANIFEST_ERROR = "manifest"
TIME_ERROR = "time"
# Each call to R2 can take its whole timeout.
PER_CALL_RESERVE_SECONDS = PUT_TIMEOUT_SECONDS + 2
# A post must not wait on R2, and a client that times out would post again. The next run writes what this one missed.
SUBMIT_TIMEOUT_SECONDS = 3
# The fallback feed is rare, but each query string is a CDN miss.
FEED_CARD_LIMIT = 1000
FEED_CARDS_CACHE_SECONDS = 60


def public_path(key: str) -> str:
    """Same-origin address of a file. `rewrites` in vercel.json sends /feeds/ to the bucket, so no CORS is involved."""
    return f"/{key}"


def manifest_url() -> str:
    """The address of the manifest, or blank when snapshots are off or storage is not set up."""
    if not conf.row().snapshots_on or get_storage() is None:
        return ""
    return public_path(MANIFEST_KEY)


def compress(body: bytes) -> bytes:
    """gzip-9 with no timestamp, so the same content always gives the same bytes."""
    return gzip.compress(body, compresslevel=9, mtime=0)


def _dump(data: object) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def city_key(channel: HomeChannel) -> str:
    """Two countries can have a city with the same slug, so state and files are keyed by both."""
    return f"{channel.country.lower()}-{deeplink.slug(channel.city)}"


def _shown_listings(channel: HomeChannel):
    """Listings people may see in this metro: active, trusted, seen this month. The same rule as `base_queryset`.

    The names narrow the query by substring, so `route()` only has to decide among close candidates.
    """
    place = Q()
    for name in channel.names():
        place |= Q(city__icontains=name) | Q(area__icontains=name)
    return (
        HomeListing.objects.filter(
            place,
            status=ListingStatus.ACTIVE,
            needs_review=False,
            post__status=PostStatus.ACCEPTED,
            country=channel.country,
            last_seen__gte=timezone.now() - timedelta(days=FEED_DAYS),
        )
        .select_related("post")
        .prefetch_related(
            Prefetch(
                "post__media",
                queryset=HomeMedia.objects.filter(status=MediaStatus.STORED).order_by("position"),
                to_attr="stored_media",
            )
        )
        .order_by("-last_seen", "-id")
    )


def city_cards(channel: HomeChannel, channels: list[HomeChannel] | None = None, *, limit: int | None = None) -> list[dict]:
    """The cards of one metro: every shown listing `route()` sends to this channel, so it matches posts and the gate."""
    channels = city_channels() if channels is None else channels
    rows = _shown_listings(channel)
    if limit is not None:
        rows = rows[:limit]
    cards = [public_card(listing, channels) for listing in rows if route(listing, channels) == channel]
    add_value_ratio(cards)
    return sorted(cards, key=lambda card: card["id"])


def feed_cards(channel: HomeChannel) -> list[dict]:
    """Cards for the fallback feed: the newest of this metro only, reused for a minute across query strings."""
    key = f"home_feed_cards:{channel.pk}"
    cards = cache.get(key)
    if cards is None:
        cards = city_cards(channel, limit=FEED_CARD_LIMIT)
        cache.set(key, cards, FEED_CARDS_CACHE_SECONDS)
    return cards


def _file_key(channel: HomeChannel, version: str) -> str:
    return f"feeds/{city_key(channel)}.{version}.json"


def _write_city(storage, channel: HomeChannel, channels: list[HomeChannel], entry: dict, now_text: str) -> dict:
    """Upload one city's file if its content changed. Returns its new entry; nothing old is deleted here."""
    cards = city_cards(channel, channels)
    version = hashlib.sha256(_dump({"schema_version": SCHEMA_VERSION, "listings": cards})).hexdigest()[:12]
    if entry.get("version") == version:
        return entry
    key = _file_key(channel, version)
    body = compress(
        _dump(
            {
                "schema_version": SCHEMA_VERSION,
                "country": channel.country,
                "city": channel.city,
                "city_slug": deeplink.slug(channel.city),
                "version": version,
                "generated_at": now_text,
                "listings": cards,
            }
        )
    )
    if len(body) > BUDGET_BYTES:
        logger.warning("snapshot_over_budget city=%s bytes=%d listings=%d", city_key(channel), len(body), len(cards))
    storage.put(key, body, JSON_TYPE, content_encoding="gzip", cache_control=FILE_CACHE)
    return {
        "version": version,
        "keys": [key, *[old for old in entry.get("keys", []) if old != key]],
        "bytes": len(body),
        "listing_count": len(cards),
    }


def _manifest(channels: list[HomeChannel], cities: dict, row, generated_at: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "alerts_on": row.alerts_enabled,
        "maintenance": {
            "on": row.maintenance_on,
            "until": row.maintenance_until.isoformat() if row.maintenance_until else None,
        },
        "cities": [
            {
                "country": channel.country,
                "city": channel.city,
                "city_slug": deeplink.slug(channel.city),
                "version": cities[city_key(channel)]["version"],
                "url": public_path(_file_key(channel, cities[city_key(channel)]["version"])),
                "listing_count": cities[city_key(channel)]["listing_count"],
                "bytes": cities[city_key(channel)]["bytes"],
            }
            for channel in channels
        ],
    }


def _out_of_time(stop_at: float | None, reserve: float = PER_CALL_RESERVE_SECONDS) -> bool:
    return stop_at is not None and time.monotonic() + reserve >= stop_at


def _prune(storage, cities: dict, stop_at: float | None) -> None:
    """Delete versions past the newest few. Only runs after the manifest stopped naming them."""
    for entry in cities.values():
        keep, extra = entry.get("keys", [])[:KEEP_VERSIONS], entry.get("keys", [])[KEEP_VERSIONS:]
        for old in list(extra):
            if _out_of_time(stop_at):
                entry["keys"] = keep + extra
                return
            try:
                storage.delete(old)
                extra.remove(old)
            except StorageError as exc:
                logger.warning("snapshot_prune_failed key=%s error=%s", old, str(exc)[:120])
        entry["keys"] = keep + extra


def _save_errors(errors: dict) -> str:
    return "; ".join(f"{name}: {text}" for name, text in sorted(errors.items()))[:300]


def _write(row, storage, channels: list[HomeChannel], targets: list[HomeChannel], whole_run: bool, stop_at: float | None) -> dict:
    out = {"ok": True, "cities": 0, "written": 0, "unchanged": 0, "failed": 0, "manifest": False}
    saved = row.snapshot_state or {}
    cities = dict(saved.get("cities") or {})
    errors = dict(saved.get("errors") or {})
    now_text = timezone.now().isoformat()
    done_all = True
    for channel in targets:
        # This city's upload and the manifest after it both have to fit.
        if _out_of_time(stop_at, 2 * PER_CALL_RESERVE_SECONDS):
            done_all = False
            out["out_of_time"] = True
            break
        key = city_key(channel)
        before = (cities.get(key) or {}).get("version")
        try:
            cities[key] = _write_city(storage, channel, channels, cities.get(key) or {}, now_text)
        except Exception as exc:  # noqa: BLE001 - one city's trouble must not stop the others or the job
            done_all = False
            errors[key] = str(exc)[:200]
            out["failed"] += 1
            logger.warning("snapshot_failed city=%s error=%s", key, str(exc)[:200])
            continue
        errors.pop(key, None)
        out["cities"] += 1
        out["written" if cities[key]["version"] != before else "unchanged"] += 1

    # With a city missing, a manifest would hide it, so people there would see a failure and not the old list.
    manifest_ready = all(city_key(channel) in cities for channel in channels)
    update: dict = {}
    if manifest_ready and not _out_of_time(stop_at):
        generated_at = now_text if whole_run else (saved.get("generated_at") or now_text)
        try:
            storage.put(MANIFEST_KEY, compress(_dump(_manifest(channels, cities, row, generated_at))), JSON_TYPE, content_encoding="gzip", cache_control=MANIFEST_CACHE)
        except Exception as exc:  # noqa: BLE001 - the old manifest and its files stay as they are
            errors[MANIFEST_ERROR] = str(exc)[:200]
            out["failed"] += 1
            logger.warning("snapshot_manifest_failed error=%s", str(exc)[:200])
        else:
            errors.pop(MANIFEST_ERROR, None)
            out["manifest"] = True
            if whole_run:
                _prune(storage, cities, stop_at)
            update["snapshot_state"] = {"cities": cities, "errors": errors, "generated_at": generated_at}
            if whole_run and done_all:
                update["snapshots_written_at"] = timezone.now()
    elif manifest_ready:
        out["out_of_time"] = True
    if "snapshot_state" not in update:
        # The manifest did not move, so neither does what we say it holds. Only the errors are new.
        update["snapshot_state"] = {**saved, "errors": errors}
    # Running out of time writes nothing, so without this a pipeline that always runs long would look like a quiet one.
    if out.get("out_of_time"):
        errors[TIME_ERROR] = "the run ended before everything was written"
        update["snapshot_state"] = {**update["snapshot_state"], "errors": errors}
        logger.warning("snapshot_out_of_time cities=%d", out["cities"])
    elif whole_run:
        errors.pop(TIME_ERROR, None)
        update["snapshot_state"] = {**update["snapshot_state"], "errors": errors}
    update["snapshot_error"] = _save_errors(errors)
    type(row).objects.filter(pk=row.pk).update(**update)
    out["ok"] = out["failed"] == 0
    return out


def write_snapshots(*, only: list[HomeChannel] | None = None, stop_at: float | None = None, timeout: float | None = None) -> dict:
    """Write what changed and the manifest. Does nothing while `snapshots_on` is off. Never raises: posting must go on."""
    try:
        row = conf.row()
        if not row.snapshots_on:
            return {"ok": True, "skipped": "snapshots are off in Home settings"}
        storage = get_storage()
        if storage is None:
            return {"ok": True, "skipped": "photo storage is not set up"}
        if timeout is not None:
            storage.timeout = timeout
        channels = city_channels()
        return _write(row, storage, channels, channels if only is None else only, only is None, stop_at)
    except Exception as exc:  # noqa: BLE001 - this job step must never end the run
        logger.exception("snapshot_run_failed")
        return {"ok": False, "error": str(exc)[:200]}


def purge_old_versions() -> dict:
    """Delete every file the manifest does not name. For taking something down now, not for routine use."""
    row = conf.row()
    storage = get_storage()
    if storage is None:
        return {"ok": False, "error": "photo storage is not set up"}
    state = dict(row.snapshot_state or {})
    cities = dict(state.get("cities") or {})
    deleted = failed = 0
    for entry in cities.values():
        current, *old = entry.get("keys", []) or [""]
        left = []
        for key in old:
            try:
                storage.delete(key)
                deleted += 1
            except StorageError as exc:
                failed += 1
                left.append(key)
                logger.warning("snapshot_purge_failed key=%s error=%s", key, str(exc)[:120])
        entry["keys"] = [current, *left] if current else left
    type(row).objects.filter(pk=row.pk).update(snapshot_state={**state, "cities": cities})
    return {"ok": failed == 0, "deleted": deleted, "failed": failed}


def after_submit(listing: HomeListing) -> None:
    """A person just posted: their city's file changes now, within a short timeout, or else at the next run.

    A file and the manifest, so at most two short calls and no retry. Never fails the post.
    """
    try:
        if not conf.row().snapshots_on or listing.kind not in ListingKind.values or listing.needs_review:
            return
        channel = route(listing, city_channels())
        if channel is not None:
            write_snapshots(only=[channel], timeout=SUBMIT_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001 - the post is saved; the next run writes the file
        logger.exception("snapshot_after_submit_failed listing=%s", listing.pk)
