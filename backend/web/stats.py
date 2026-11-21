"""Public market statistics: the numbers behind the public site (PLAN §4), aggregates only.

Same arithmetic as the channel digests (`home.content.data`, CONTENT-SPEC §4):
- windows read `post.posted_at`, weeks are local Monday 00:00 to Monday 00:00 in the city's clock;
- reposts are removed before any count or median (`data.dedupe`);
- a median needs n >= 8 (else null) and drops prices outside p5–p95 when n >= 20 (`data.medians`);
- no week before 2026-11-02 is ever shown (the digests' numbers start there).

Never in the output: post text, contact, poster, source group or channel names of sources. Courier: counts only.
"""

from __future__ import annotations

import collections
import csv
import io
import statistics
from datetime import date, datetime, time, timedelta
from datetime import timezone as dt_timezone

from django.core.cache import cache
from django.db.models import Count, Q
from django.utils import timezone

from home import deeplink
from home.alerts import city_zone
from home.content import data
from home.content.monthly import COUNTRY_ORDER
from home.feed import base_queryset, city_names, city_q
from home.models import HomeChannel, HomePost, HomeSource, ListingKind

METHOD_VERSION = "1"
FIRST_WEEK = date(2026, 11, 2)
MAX_WEEKS = 12
AREA_MIN_N = 4
AREA_LIMIT = 10
ROUTE_LIMIT = 20
CACHE_KEY = "web:public_stats:v1"
CACHE_SECONDS = 3600


def _round(value: float) -> int:
    return int(value + 0.5)


def _priced(rows: list[data.Row], kind: str, currency: str) -> list[float]:
    return sorted(r.price for r in rows if r.monthly_priced and r.type == kind and r.currency == currency and r.mode != "short_term")


def _trimmed(prices: list[float]) -> list[float]:
    n = len(prices)
    if n >= data.TRIM_FROM_N:
        low, high = prices[int(0.05 * (n - 1))], prices[int(round(0.95 * (n - 1)))]
        return [p for p in prices if low <= p <= high]
    return prices


def type_medians(rows: list[data.Row], currency: str, *, quartiles: bool) -> list[dict]:
    """One entry per type. `median` (and p25/p75) are null under n = 8, as in the digests."""
    meds = data.medians(rows, currency)
    out = []
    for kind in data.TYPES:
        prices = _priced(rows, kind, currency)
        entry: dict = {"type": kind, "n": len(prices), "median": None}
        if kind in meds:
            entry["median"] = _round(meds[kind][1])
        if quartiles:
            entry["p25"] = entry["p75"] = None
            if kind in meds:
                kept = _trimmed(prices)
                q1, _q2, q3 = statistics.quantiles(kept, n=4, method="inclusive")
                entry["p25"], entry["p75"] = _round(q1), _round(q3)
        out.append(entry)
    return out


def _week_starts(now_local: datetime) -> list[datetime]:
    """Local Mondays of the complete weeks, oldest first, never before FIRST_WEEK, at most MAX_WEEKS."""
    this_monday = now_local.date() - timedelta(days=now_local.weekday())
    first = max(FIRST_WEEK, this_monday - timedelta(weeks=MAX_WEEKS))
    first -= timedelta(days=first.weekday())
    out, day = [], first
    while day < this_monday:
        out.append(datetime.combine(day, time(0), tzinfo=now_local.tzinfo))
        day += timedelta(weeks=1)
    return out


def _areas(rows: list[data.Row], channel: HomeChannel, currency: str) -> list[dict]:
    by_area: dict[str, list[data.Row]] = collections.defaultdict(list)
    for row in rows:
        if row.kind != ListingKind.OFFER or row.type != "room" or not row.area:
            continue
        if not data._CLEAN_AREA.match(row.area) or row.area.casefold() == channel.city.casefold():
            continue
        by_area[row.area.casefold()].append(row)
    out = []
    for found in by_area.values():
        name = found[0].area
        name = name if any(ch.isupper() for ch in name) else name.title()  # catalog keys are lower case
        if len(found) < AREA_MIN_N:
            continue
        room = data.medians(found, currency).get("room")
        out.append({"name": name, "n": len(found), "median_room": _round(room[1]) if room else None})
    out.sort(key=lambda item: (-item["n"], item["name"]))
    return out[:AREA_LIMIT]


def _share(part: int, whole: int) -> float:
    return round(part / whole, 3) if whole else 0.0


def city_stats(channel: HomeChannel, now: datetime, channels: list[HomeChannel], names: dict[str, str]) -> dict:
    zone = city_zone(channel.country, channel.city)
    now_local = now.astimezone(zone)
    currency = data.CURRENCY.get(channel.country, "")
    weeks = _week_starts(now_local)
    start = min([now - timedelta(days=data.MEDIAN_DAYS), *weeks])
    every = data.rows_for(channel, start, now, channels)
    month = data.dedupe([r for r in every if r.posted_at >= now - timedelta(days=data.MEDIAN_DAYS)])
    day = data.dedupe([r for r in month if r.posted_at > now - timedelta(hours=24)], similar=True)
    week7 = [r for r in month if r.posted_at > now - timedelta(days=7)]
    offers = [r for r in month if r.kind == ListingKind.OFFER]

    weekly = []
    for monday in weeks:
        end = monday + timedelta(weeks=1)
        rows = data.dedupe([r for r in every if monday <= r.posted_at < end])
        weekly.append(
            {
                "week_start": monday.date().isoformat(),
                "new": len(rows),
                "medians": type_medians(rows, currency, quartiles=False),
            }
        )

    by_weekday = [0] * 7  # 0 = Monday, in the city's clock
    for row in month:
        by_weekday[row.posted_at.astimezone(zone).weekday()] += 1

    live = {}
    for kind, key in ((ListingKind.OFFER, "offers"), (ListingKind.WANTED, "wanted")):
        filters = {"country": channel.country, "kind": kind, "city": channel.city}
        live[key] = base_queryset(filters).filter(city_q(filters)).count()

    return {
        "slug": deeplink.slug(channel.city),
        "name_fa": names.get(channel.city.casefold(), channel.city),
        "name_en": channel.city,
        "country": channel.country,
        "currency": currency,
        "live": live,
        "new_24h": len(day),
        "new_7d": len(week7),
        "new_30d": len(month),
        "medians_30d": type_medians(month, currency, quartiles=True),
        "weekly": weekly,
        "areas_30d": _areas(month, channel, currency),
        "by_weekday": by_weekday,
        "with_photo_share": _share(sum(1 for r in offers if r.photos > 0), len(offers)),
        "furnished_share": _share(sum(1 for r in offers if r.listing.furnished == "yes"), len(offers)),
    }


def city_channels() -> list[HomeChannel]:
    """Enabled city channels in the site's order: Canada then Germany, each in COUNTRY_ORDER."""
    found = list(HomeChannel.objects.filter(enabled=True).exclude(city=""))

    def order(channel: HomeChannel) -> tuple:
        slugs = COUNTRY_ORDER.get(channel.country, [])
        slug = deeplink.slug(channel.city)
        return (channel.country != "CA", channel.country, slugs.index(slug) if slug in slugs else len(slugs), slug)

    return sorted(found, key=order)


def courier_stats(now: datetime) -> dict:
    from item_requests.explore import public_open_requests_queryset
    from item_requests.models import HIDDEN_POST_REASONS, City, ItemRequest, RequestType

    live = public_open_requests_queryset().values("type").annotate(n=Count("id"))
    counts = {row["type"]: row["n"] for row in live}
    recent = (
        ItemRequest.objects.filter(created_at__gte=now - timedelta(days=30))
        .exclude(unmatchable_reason__in=HIDDEN_POST_REASONS)
        .values("origin_country", "origin_city", "destination_country", "destination_city")
        .annotate(
            trips=Count("id", filter=Q(type=RequestType.SUPPLY)),
            requests=Count("id", filter=Q(type=RequestType.DEMAND)),
        )
    )
    names = {(code, slug): name for code, slug, name in City.objects.values_list("country__code", "slug", "name_en")}

    def name(country: str, slug: str) -> str:
        return names.get((country, slug)) or slug.replace("-", " ").title()

    routes = [
        {
            "from": name(row["origin_country"], row["origin_city"]),
            "to": name(row["destination_country"], row["destination_city"]),
            "trips": row["trips"],
            "requests": row["requests"],
        }
        for row in recent
    ]
    routes.sort(key=lambda item: (-(item["trips"] + item["requests"]), item["from"], item["to"]))
    return {
        "live_trips": counts.get(RequestType.SUPPLY, 0),
        "live_requests": counts.get(RequestType.DEMAND, 0),
        "routes_30d": routes[:ROUTE_LIMIT],
    }


def build(now: datetime | None = None) -> dict:
    now = now or timezone.now()
    channels = city_channels()
    every_channel = list(HomeChannel.objects.filter(enabled=True).exclude(city=""))
    names = city_names()
    cities = [city_stats(channel, now, every_channel, names) for channel in channels]
    return {
        "generated_at": now.astimezone(dt_timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "method_version": METHOD_VERSION,
        "cities": cities,
        "courier": courier_stats(now),
        "totals": {
            "sources_read": HomeSource.objects.filter(enabled=True).count(),
            "posts_reviewed_30d": HomePost.objects.filter(reviewed_at__gte=now - timedelta(days=30)).count(),
            "listings_live": sum(city["live"]["offers"] + city["live"]["wanted"] for city in cities),
        },
    }


def cached() -> dict:
    found = cache.get(CACHE_KEY)
    if found is None:
        found = build()
        cache.set(CACHE_KEY, found, CACHE_SECONDS)
    return found


CSV_COLUMNS = ["week_start", "city", "currency", "new"] + [f"{kind}_{part}" for kind in data.TYPES for part in ("n", "median")]


def city_csv(stats: dict, slug: str) -> str | None:
    city = next((item for item in stats["cities"] if item["slug"] == slug), None)
    if city is None:
        return None
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for week in city["weekly"]:
        by_type = {entry["type"]: entry for entry in week["medians"]}
        cells = [week["week_start"], city["name_en"], city["currency"], week["new"]]
        for kind in data.TYPES:
            entry = by_type.get(kind) or {"n": 0, "median": None}
            cells += [entry["n"], "" if entry["median"] is None else entry["median"]]
        writer.writerow(cells)
    return out.getvalue()
