"""Second pass for listings whose text was not enough: read the photos.

Many posts carry the price, size or address only in the picture (a screenshot of a
listing, a floor plan with a note). For a listing that still has gaps, the AI gets
the caption, what was already extracted, and up to four stored photos, and returns
the same JSON as the text review. Text always wins: the photos only fill empty
fields. Needs HOME_VISION_MODEL (an image-capable model id) and photo storage.
"""

from __future__ import annotations

import logging
import time

from django.utils import timezone

from ai.llm import chat, llm_enabled
from home import conf
from home.models import HomeListing, HomeMedia, ListingStatus, MediaStatus, PostStatus
from home.review import SYSTEM_PROMPT, build_prompt, normalize_listing, parse_json
from home.storage import get_storage

logger = logging.getLogger(__name__)

MAX_IMAGES = 4
SHORT_TEXT_CHARS = 120
CALL_RESERVE_SECONDS = 8

VISION_ADDENDUM = """

You also receive photos of the same post. Use them only to fill fields the caption leaves empty:
a price, size, rooms, address or area written in a screenshot, floor plan or sign. Do not describe the photos.
Never override a value the caption already gives. If the photos clearly show something other than housing
(a banner, a coupon, a person), reply {"accept": false, "reject_reason": "ad"}. Reply with the same JSON as before."""


def vision_model() -> str:
    return conf.vision_model()


def vision_enabled() -> bool:
    return conf.step_enabled("vision") and bool(vision_model()) and llm_enabled() and get_storage() is not None


def vision_candidates():
    """Active listings with stored photos that still have gaps, not yet read by the vision pass."""
    from django.db.models import Q

    return (
        HomeListing.objects.filter(
            status=ListingStatus.ACTIVE,
            vision_at__isnull=True,
            post__status=PostStatus.ACCEPTED,
            post__media__status=MediaStatus.STORED,
        )
        .filter(Q(needs_review=True) | Q(kind="offer", price_amount__isnull=True))
        .select_related("post", "post__source")
        .distinct()
        .order_by("-last_seen")
    )


def merge_vision(existing: dict, seen: dict) -> dict:
    """Fill empty fields of the text result from the photo result. Text values always win."""
    merged = dict(seen)
    for key, value in (existing or {}).items():
        if key == "flags":
            merged["flags"] = {**(seen.get("flags") or {}), **(value or {})}
        elif value not in (None, "", [], {}):
            merged[key] = value
    merged["accept"] = True
    return merged


def enrich_listing(listing: HomeListing) -> str:
    """Read the photos of one listing. Returns "ok", "rejected", or an error text."""
    storage = get_storage()
    post = listing.post
    urls = [
        storage.url(key)
        for key in post.media.filter(status=MediaStatus.STORED).order_by("position").values_list("storage_key", flat=True)[:MAX_IMAGES]
        if key
    ]
    if storage is None or not urls:
        return "no photos"
    prompt = build_prompt(post) + "\n\nAlready extracted (empty means unknown):\n" + _brief(listing)
    content = [{"type": "text", "text": prompt}] + [{"type": "image_url", "image_url": {"url": url}} for url in urls]
    result = chat(
        [{"role": "system", "content": SYSTEM_PROMPT + VISION_ADDENDUM}, {"role": "user", "content": content}],
        model=vision_model(),
        temperature=0,
        max_tokens=1200,
    )
    flags = dict(listing.flags or {})
    listing.vision_at = timezone.now()
    if not result.ok:
        flags["vision"] = "failed"
        listing.flags = flags
        listing.save(update_fields=["vision_at", "flags", "updated_at"])
        return result.error or "vision call failed"
    seen = parse_json(result.text)
    if seen is None:
        flags["vision"] = "failed"
        listing.flags = flags
        listing.save(update_fields=["vision_at", "flags", "updated_at"])
        return "Invalid model JSON."
    if not seen.get("accept", True):
        flags["vision"] = "not_housing"
        listing.flags = flags
        listing.needs_review = True
        listing.review_note = "photos do not look like housing"
        listing.save(update_fields=["vision_at", "flags", "needs_review", "review_note", "updated_at"])
        return "rejected"
    merged = merge_vision(post.review_json or {}, seen)
    fields, notes = normalize_listing(post, merged)
    if fields is None:
        flags["vision"] = "failed"
        listing.flags = flags
        listing.save(update_fields=["vision_at", "flags", "updated_at"])
        return "unusable result"
    fields["flags"] = {**fields.get("flags", {}), "vision": "ok"}
    for name, value in fields.items():
        setattr(listing, name, value)
    listing.needs_review = bool(notes)
    listing.review_note = "; ".join(notes)[:255]
    listing.save()
    return "ok"


def enrich_pending(*, limit: int = 5, stop_at: float | None = None) -> dict:
    if not vision_enabled():
        return {"ok": True, "skipped": "vision is not configured", "enriched": 0}
    done = failed = 0
    for listing in vision_candidates()[:limit]:
        if stop_at is not None and time.monotonic() + CALL_RESERVE_SECONDS >= stop_at:
            break
        outcome = enrich_listing(listing)
        if outcome in {"ok", "rejected"}:
            done += 1
        else:
            failed += 1
            logger.warning("Vision pass failed for listing %s: %s", listing.pk, outcome)
    return {"ok": True, "enriched": done, "failed": failed, "remaining": vision_candidates().count()}


def _brief(listing: HomeListing) -> str:
    parts = {
        "kind": listing.kind,
        "property_type": listing.property_type,
        "city": listing.city,
        "area": listing.area,
        "price_amount": listing.price_amount,
        "price_currency": listing.price_currency,
        "size_m2": listing.size_m2,
        "rooms": listing.rooms,
        "available_from": listing.available_from,
    }
    return "; ".join(f"{key}={value if value not in (None, '') else ''}" for key, value in parts.items())
