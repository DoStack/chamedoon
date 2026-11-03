"""Neighbourhoods: which `HomeArea` a listing's free-text place falls in, and the counts and search of F07.

A listing keeps its text `area`. The link only adds a name to filter on and a point for the map. A listing that matches
nothing stays unlinked and shows only under «همه‌جا».
"""

from __future__ import annotations

import logging
import re
import time
from datetime import timedelta

from django.utils import timezone

from home import deeplink
from home.models import HomeArea, HomeChannel, HomeListing
from home.places import fold, split_areas, toronto_district
from home.publish import city_channels, route

logger = logging.getLogger(__name__)

BACKFILL_BATCH = 300
SEARCH_LIMIT = 10
MIN_PARTIAL = 4
TORONTO = "toronto"
WEEK = timedelta(days=7)
_PERSIAN_AND = re.compile(r"\s+و\s+")


def area_names(area: HomeArea) -> set[str]:
    """Every folded name that means this area."""
    names = {area.canonical, area.fa, *area.aliases.split(",")}
    return {fold(name) for name in names if name and name.strip()}


def _rows(channel: HomeChannel) -> list[HomeArea]:
    return list(HomeArea.objects.filter(country=channel.country, city__iexact=channel.city))


def _match(texts: list[str], rows: list[HomeArea], *, toronto: bool) -> HomeArea | None:
    names = {row.pk: area_names(row) for row in rows}
    folded = [fold(text) for text in texts if text]
    for value in folded:
        for row in rows:
            if value in names[row.pk]:
                return row
    for value in folded:
        padded = f" {value} "
        for row in rows:
            if any(len(name) >= MIN_PARTIAL and f" {name} " in padded for name in names[row.pk]):
                return row
    if toronto:
        for value in folded:
            district = toronto_district(value)
            if district:
                return next((row for row in rows if row.canonical == district), None)
    return None


def match_area(listing: HomeListing, channels: list[HomeChannel] | None = None) -> HomeArea | None:
    channel = route(listing, city_channels() if channels is None else channels)
    if channel is None:
        return None
    texts = [*split_areas(listing.area), listing.area, listing.city]
    return _match(texts, _rows(channel), toronto=deeplink.slug(channel.city) == TORONTO)


def link_area(listing: HomeListing, channels: list[HomeChannel] | None = None) -> None:
    """Set the listing's area. Never fails the post or the review that called it."""
    try:
        area = match_area(listing, channels)
        now = timezone.now()
        HomeListing.objects.filter(pk=listing.pk).update(home_area=area, area_checked_at=now)
        listing.home_area, listing.area_checked_at = area, now
    except Exception:  # noqa: BLE001 - the backfill tries again, since area_checked_at stays empty
        logger.exception("area_link_failed listing=%s", listing.pk)


def backfill_listing_areas(*, limit: int = BACKFILL_BATCH, stop_at: float | None = None) -> dict:
    """Link listings not looked at yet, a batch per run, so a migration never holds the table."""
    channels = city_channels()
    out = {"ok": True, "checked": 0, "linked": 0}
    for listing in HomeListing.objects.filter(area_checked_at__isnull=True).select_related("post").order_by("pk")[:limit]:
        if stop_at is not None and time.monotonic() >= stop_at:
            break
        link_area(listing, channels)
        out["checked"] += 1
        out["linked"] += 1 if listing.home_area_id else 0
    return out


def city_areas(channel: HomeChannel, cards: list[dict]) -> dict:
    """F07 and F09: the areas of a metro by this week's count, and how many listings have none."""
    offers = [card for card in cards if card["kind"] == "offer"]
    total: dict[int, int] = {}
    week: dict[int, int] = {}
    since = (timezone.now() - WEEK).isoformat()
    for card in offers:
        area_id = card["area_id"]
        if area_id:
            total[area_id] = total.get(area_id, 0) + 1
            if card["last_seen"] >= since:
                week[area_id] = week.get(area_id, 0) + 1
    rows = [
        {
            "id": row.pk,
            "canonical": row.canonical,
            "name_fa": row.fa,
            "group": row.group,
            "lat": float(row.lat) if row.lat is not None else None,
            "lng": float(row.lng) if row.lng is not None else None,
            "listing_count": total.get(row.pk, 0),
            "week_count": week.get(row.pk, 0),
        }
        for row in _rows(channel)
    ]
    rank = {row.pk: row.rank for row in _rows(channel)}
    rows.sort(key=lambda row: (-row["week_count"], -row["listing_count"], rank[row["id"]], row["id"]))
    return {
        "country": channel.country,
        "city": channel.city,
        "areas": rows,
        "no_area_count": sum(1 for card in offers if not card["area_id"]),
    }


def _search_key(text: str) -> str:
    return fold(_PERSIAN_AND.sub(" and ", text or ""))


def search_areas(query: str) -> list[tuple[HomeArea, str]]:
    """Areas by name or other name, in every city. A Toronto intersection nobody listed reaches its district."""
    wanted = _search_key(query)
    if not wanted:
        return []
    found: list[tuple[HomeArea, str]] = []
    rows = list(HomeArea.objects.all())
    for row in rows:
        hit = next((name for name in sorted(area_names(row), key=len) if wanted in name), "")
        if hit:
            found.append((row, hit))
    if not found:
        district = toronto_district(wanted)
        if district:
            found += [(row, query.strip()) for row in rows if row.canonical == district and deeplink.slug(row.city) == TORONTO]
    return found[:SEARCH_LIMIT]
