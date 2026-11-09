"""Neighbourhoods: which `HomeArea` a listing's free-text place falls in, and the counts and search of F07.

A listing keeps its text `area`. The link only adds a name to filter on and a point for the map, and a wrong point is
wrong data, so matching is strict: a whole name, or a part only when that part is a name of two or more words. A single
word like "Markham" must be the whole place, since "Markham Rd" is a street in Scarborough. German names rarely stand for
anything else, so in Germany one word counts inside a text too, unless the next word makes it a street.

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
from home.city_names import is_place
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
# Between places: «Haselhorst (Spandau)», «Aurora - Henderson», «Porz; Kalk», «Langenhorn to Poppenbüttel».
_PLACE_SPLIT = re.compile(r"\s*(?:[()\[\];:·]|\s[-–—]\s|\sto\s|\sbis\s)\s*")
_LEADING_WORDS = re.compile(r"^(?:near|nearby|close to|around|next to|opposite|by|nahe|bei|intersection of)\s+", re.IGNORECASE)
_TRAILING_WORDS = re.compile(r"\s+(?:area|intersection)$", re.IGNORECASE)
_STREET_SUFFIXES = {"rd", "road", "st", "ave", "blvd", "boulevard", "dr", "drive", "way", "cres", "crescent", "ct", "court", "ln", "lane", "pl", "place", "hwy", "str", "strasse", "allee", "weg", "platz", "chaussee", "damm", "ring", "rue", "chemin"}
_STREET_WORDS = {"and", "at", "near", *_STREET_SUFFIXES, "e", "w", "n", "s", "east", "west", "north", "south"}
# Words for a part of any town, so they count only as the whole place: "Downtown Aurora" and "Krefeld city center" are not this metro's centre.
_GENERIC = {"downtown", "center", "centre", "city center", "city centre", "zentrum", "altstadt", "neustadt", "uptown", "mitte"}
# Spellings seen in real posts, word by word, before matching.
SPELLINGS = {
    TORONTO: {
        "young": "yonge", "yang": "yonge", "yung": "yonge", "yong": "yonge",
        "stiles": "steeles", "steles": "steeles", "stilles": "steeles", "steels": "steeles",
        "shepherd": "sheppard", "sheppered": "sheppard", "shepard": "sheppard",
        "dan": "don", "mckenzie": "mackenzie", "commer": "cummer",
    },
}


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


def _parts(text: str) -> list[str]:
    """The places a text names, folded: brackets, slashes, «یا», or and a spaced dash separate them, a leading 'near' goes."""
    found: list[str] = []
    for part in split_areas(_PLACE_SPLIT.sub(",", text or "")):
        value = fold(_TRAILING_WORDS.sub("", _LEADING_WORDS.sub("", part.strip())))
        if value and value not in found:
            found.append(value)
    return found


def _street_key(value: str) -> str:
    """An intersection in any order: «Eglinton & Yonge», «Yonge - Eglinton» and «Yonge St and Eglinton Ave» are one."""
    return " ".join(sorted(word for word in value.split() if word not in _STREET_WORDS))


def _spelled(value: str, spellings: dict[str, str]) -> str:
    return " ".join(spellings.get(word, word) for word in value.split())


def _inside(name: str, value: str) -> bool:
    """A whole name inside a longer text, unless the next word makes it a street: Markham Rd, Bayview Village Dr."""
    start = f" {value} ".find(f" {name} ")
    if start < 0:
        return False
    rest = value[start + len(name) :].split()
    return not (rest and rest[0] in _STREET_SUFFIXES)


class AreaIndex:
    """One metro's names, sorted for matching. A batch builds it once and asks it many times."""

    def __init__(self, channel: HomeChannel, rows: list[HomeArea]) -> None:
        self.rows = rows
        self.metro = _metro_key(channel.city)
        self.single_words_inside = channel.country != "CA"
        self.spellings = SPELLINGS.get(self.metro, {})
        self.names: dict[str, HomeArea] = {}
        self.streets: dict[str, HomeArea] = {}
        for row in rows:
            for name in area_names(row):
                if " and " in name:
                    self.streets.setdefault(_street_key(name), row)
                else:
                    self.names.setdefault(name, row)
        self.longest = sorted(self.names, key=lambda name: (-len(name), name))
        # Towns of their own: only these can overrule a city that is itself a row.
        routed = {fold(name) for name in channel.names()}
        self.towns = [row for row in rows if area_names(row) & routed]

    def named(self, area: str) -> HomeArea | None:
        """A row this area names exactly (one of its comma parts), never a generic word or a street guess."""
        area = area if is_place(area) else ""
        for value in (_spelled(part, self.spellings) for part in _parts(area)):
            row = self.names.get(value)
            if row is not None and value not in _GENERIC:
                return row
        return None

    def _allowed(self, row: HomeArea, city_row: HomeArea | None) -> bool:
        """A city that is a row wins over a neighbourhood: Coquitlam stays Coquitlam for «Shaughnessy», a Vancouver area."""
        return city_row is None or row is city_row or any(row is town for town in self.towns)

    def match(self, area: str, city: str) -> HomeArea | None:
        area = area if is_place(area) else ""
        parts = [_spelled(part, self.spellings) for part in _parts(area)]
        whole = _spelled(fold(area), self.spellings)
        city_row = self.names.get(fold(city)) if city and _metro_key(city) != self.metro else None
        for value in parts:
            row = self.names.get(value)
            if row is not None and self._allowed(row, city_row):
                return row
        for value in parts:
            for name in self.longest:
                if name in _GENERIC or (" " not in name and not self.single_words_inside):
                    continue
                if _inside(name, value) and self._allowed(self.names[name], city_row):
                    return self.names[name]
        if city_row is not None:
            return city_row
        for value in [*parts, whole]:
            row = self.streets.get(_street_key(value)) if value else None
            if row is not None:
                return row
        if self.metro == TORONTO:
            for value in [*parts, whole]:
                district = toronto_district(value)
                # A street like Queen St runs far past downtown, so a street alone does not place a listing there.
                if district and (district != "downtown" or "downtown" in value):
                    return next((row for row in self.rows if row.canonical == district), None)
        return None


def match_area(listing: HomeListing, channels: list[HomeChannel] | None = None) -> HomeArea | None:
    channel = route(listing, city_channels() if channels is None else channels)
    if channel is None:
        return None
    return AreaIndex(channel, metro_rows(channel)).match(listing.area, listing.city)


REGIONS = {("CA", "toronto"): "ON", ("CA", "montreal"): "QC", ("CA", "vancouver"): "BC"}


def area_metro(listing: HomeListing, channels: list[HomeChannel] | None = None) -> tuple[HomeChannel | None, str]:
    """The metro the stored area belongs to, and a verdict: "" (it agrees, or the area is unknown), "moved" (the area
    names a place of exactly one other metro, so that metro wins over the group's city), "unclear" (several do).

    Audit 2026-11-09, #308: a Toronto room («Bayview Village») posted in the Montreal group was stored as Montreal."""
    channels = city_channels() if channels is None else channels
    own = route(listing, channels)
    area = (listing.area or "").strip()
    if own is None or not area:
        return own, ""
    if AreaIndex(own, metro_rows(own)).match(area, listing.city) is not None:
        return own, ""
    others = [
        channel
        for channel in channels
        if channel.country == listing.country and channel.pk != own.pk and AreaIndex(channel, metro_rows(channel)).named(area) is not None
    ]
    if len(others) == 1:
        return others[0], "moved"
    return own, "unclear" if others else ""


def move_to_metro(listing: HomeListing, channel: HomeChannel) -> None:
    """The listing takes the metro its area names; its region follows."""
    region = REGIONS.get((channel.country, _metro_key(channel.city)), listing.region if channel.country != "CA" else "")
    logger.info("area_city_moved listing=%s from=%s to=%s area=%s", listing.pk, listing.city, channel.city, listing.area)
    HomeListing.objects.filter(pk=listing.pk).update(city=channel.city, region=region, updated_at=timezone.now())
    listing.city, listing.region = channel.city, region


def link_area(listing: HomeListing, channels: list[HomeChannel] | None = None) -> None:
    """Set the listing's area. Never fails what called it, even inside a transaction: the savepoint holds any DB error."""
    try:
        channels = city_channels() if channels is None else channels
        target, verdict = area_metro(listing, channels)
        if verdict == "moved":
            # The area outranks the group's city (review and every later save).
            move_to_metro(listing, target)
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
