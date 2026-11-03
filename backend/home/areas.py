"""Neighbourhoods: which `HomeArea` a listing's free-text place falls in, and the counts and search of F07.

A listing keeps its text `area`. The link only adds a name to filter on and a point for the map, and a wrong point is
wrong data, so matching is strict: a whole name, or a part only when that part is a name of two or more words. A single
word like "Markham" must be the whole place, since "Markham Rd" is a street in Scarborough.

A listing that matches nothing stays unlinked and shows only under «همه‌جا». Changing an area or a channel marks every
listing for another look in `HomeSettings.areas_changed_at`, and the job works through them in batches, so a save in the
admin never rewrites the table.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from home import conf, deeplink
from home.models import HomeArea, HomeChannel, HomeListing, HomeSettings
from home.places import fold, split_areas, toronto_district
from home.publish import city_channels, route

logger = logging.getLogger(__name__)

BACKFILL_BATCH = 300
SEARCH_LIMIT = 10
MIN_PARTIAL_WORDS = 2
TORONTO = "toronto"
WEEK = timedelta(days=7)
# The same metro by another name. Seed rows say Cologne; a channel could say Köln.
METRO_SAME = {"koln": "cologne"}
_PERSIAN_AND = re.compile(r"\s+و\s+")


def _metro_key(name: str) -> str:
    slug = deeplink.slug(name)
    return METRO_SAME.get(slug, slug)


def area_names(area: HomeArea) -> set[str]:
    """Every folded name that means this area."""
    names = {area.canonical, area.fa, *area.aliases.split(",")}
    return {fold(name) for name in names if name and name.strip()}


def metro_rows(channel: HomeChannel) -> list[HomeArea]:
    wanted = _metro_key(channel.city)
    return [row for row in HomeArea.objects.filter(country=channel.country) if _metro_key(row.city) == wanted]


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
            if any(len(name.split()) >= MIN_PARTIAL_WORDS and f" {name} " in padded for name in names[row.pk]):
                return row
    if toronto:
        for value in folded:
            district = toronto_district(value)
            # A street like Queen St runs far past downtown, so a street alone does not place a listing there.
            if district and (district != "downtown" or "downtown" in value):
                return next((row for row in rows if row.canonical == district), None)
    return None


def match_area(listing: HomeListing, channels: list[HomeChannel] | None = None) -> HomeArea | None:
    channel = route(listing, city_channels() if channels is None else channels)
    if channel is None:
        return None
    texts = [*split_areas(listing.area), listing.area, listing.city]
    return _match(texts, metro_rows(channel), toronto=_metro_key(channel.city) == TORONTO)


def link_area(listing: HomeListing, channels: list[HomeChannel] | None = None) -> None:
    """Set the listing's area. Never fails what called it, even inside a transaction: the savepoint holds any DB error."""
    try:
        channels = city_channels() if channels is None else channels
        channel = route(listing, channels)
        if channel is not None and not metro_rows(channel):
            # No areas for this metro yet: leave it unstamped, so it links once they exist.
            return
        area = match_area(listing, channels)
        now = timezone.now()
        with transaction.atomic():
            HomeListing.objects.filter(pk=listing.pk).update(home_area=area, area_checked_at=now)
        listing.home_area, listing.area_checked_at = area, now
    except Exception:  # noqa: BLE001 - the backfill tries again, since area_checked_at stays as it was
        logger.exception("area_link_failed listing=%s", listing.pk)


def mark_areas_changed() -> None:
    """An area or channel changed. Only a timestamp: the job re-links listings older than it, a batch per run."""
    HomeSettings.objects.update(areas_changed_at=timezone.now(), area_backfill_after=0)


def backfill_listing_areas(*, limit: int = BACKFILL_BATCH, stop_at: float | None = None) -> dict:
    """Link listings not looked at since the areas last changed. The cursor keeps unlinkable rows from filling each batch."""
    row = conf.row()
    channels = city_channels()
    out = {"ok": True, "checked": 0, "linked": 0}
    stale = Q(area_checked_at__isnull=True)
    if row.areas_changed_at:
        stale |= Q(area_checked_at__lt=row.areas_changed_at)
    last = row.area_backfill_after
    batch = list(HomeListing.objects.filter(stale, pk__gt=last).select_related("post").order_by("pk")[:limit])
    for listing in batch:
        if stop_at is not None and time.monotonic() >= stop_at:
            break
        link_area(listing, channels)
        last = listing.pk
        out["checked"] += 1
        out["linked"] += 1 if listing.home_area_id else 0
    HomeSettings.objects.filter(pk=row.pk).update(area_backfill_after=0 if len(batch) < limit and out["checked"] == len(batch) else last)
    return out


def city_areas(channel: HomeChannel, cards: list[dict]) -> dict:
    """F07 and F09: the areas of a metro by this week's count, and how many listings have none."""
    offers = [card for card in cards if card["kind"] == "offer"]
    total: dict[int, int] = {}
    week: dict[int, int] = {}
    since = (timezone.now() - WEEK).isoformat()
    for card in offers:
        area_id = card.get("area_id")
        if area_id:
            total[area_id] = total.get(area_id, 0) + 1
            if card["last_seen"] >= since:
                week[area_id] = week.get(area_id, 0) + 1
    areas = metro_rows(channel)
    rank = {row.pk: row.rank for row in areas}
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
        for row in areas
    ]
    rows.sort(key=lambda row: (-row["week_count"], -row["listing_count"], rank[row["id"]], row["id"]))
    return {
        "country": channel.country,
        "city": channel.city,
        "areas": rows,
        "no_area_count": sum(1 for card in offers if not card.get("area_id")),
    }


def _search_key(text: str) -> str:
    return fold(_PERSIAN_AND.sub(" and ", text or ""))


def search_areas(query: str, here: HomeChannel | None = None) -> dict[str, list[tuple[HomeArea, str]]]:
    """Areas by name or other name, split into this city and the others, each list capped on its own.

    A Toronto intersection nobody listed reaches its district.
    """
    found: dict[str, list[tuple[HomeArea, str]]] = {"here": [], "elsewhere": []}
    wanted = _search_key(query)
    if not wanted:
        return found
    here_key = (here.country, _metro_key(here.city)) if here is not None else None
    rows = list(HomeArea.objects.all())

    def add(row: HomeArea, matched: str) -> None:
        found["here" if (row.country, _metro_key(row.city)) == here_key else "elsewhere"].append((row, matched))

    for row in rows:
        hit = next((name for name in sorted(area_names(row), key=len) if wanted in name), "")
        if hit:
            add(row, hit)
    if not found["here"] and not found["elsewhere"]:
        district = toronto_district(wanted)
        for row in rows:
            if district and row.canonical == district and _metro_key(row.city) == TORONTO:
                add(row, query.strip())
    return {side: hits[:SEARCH_LIMIT] for side, hits in found.items()}
