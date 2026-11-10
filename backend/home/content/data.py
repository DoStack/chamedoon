"""Numbers of a daily digest, straight from the listings (spec §2.0, §2.1, §2.2, §4).

Rules that matter, each found on real data:
- Windows read `post.posted_at` (when the ad appeared), never `listing.created_at` (when we read it).
- Reposts are removed before any count or median: `canonical_id` misses them (2026-11-10: Toronto 23 -> 22).
- The model's property type is often wrong; a listing becomes the deal only when its text agrees (type guard).
"""

from __future__ import annotations

import collections
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from django.db.models import Count, Q

from home.contact_text import safe_area
from home.models import HomeChannel, HomeListing, ListingKind, ListingStatus, MediaStatus, PostStatus
from home.publish import rent_basis, route

MEDIAN_DAYS = 30
MIN_MEDIAN_N = 8
TRIM_FROM_N = 20
DEAL_MIN, DEAL_MAX = 0.15, 0.60
MIN_DAILY = 3
TYPES = ("room", "basement", "studio", "apartment", "house")
CURRENCY = {"CA": "CAD", "DE": "EUR"}
BASE_STATUSES = (ListingStatus.ACTIVE, ListingStatus.EXPIRED, ListingStatus.RENTED)

# «اتاق» on its own means a room; «اتاق خواب» / «۲ اتاق» describe a flat.
_ROOM_WORD = re.compile(r"(?<![0-9۰-۹]\s)(?<![0-9۰-۹])اتاق(?![‌\s]*خواب)(?![‌]?ه)")
_FLAT_WORDS = re.compile(r"(آپارتمان|کاندو|واحد|یونیت|unit|condo|apartment|wohnung)\s", re.IGNORECASE)
_BASEMENT_WORD = re.compile(r"(بیسمنت|بيسمنت|زیرزمین|basement)", re.IGNORECASE)
_WHOLE_PLACE = re.compile(r"(کل\s*(خانه|خونه|واحد|آپارتمان|بیسمنت)|تمام\s*(خانه|واحد)|whole\s+(unit|house|place)|entire)", re.IGNORECASE)


@dataclass
class Row:
    id: int
    kind: str
    type: str
    price: float
    currency: str
    period: str
    mode: str
    basis: str
    area: str
    area_fa: str
    area_name: str
    text: str
    posted_at: datetime
    photos: int
    status: str
    listing: HomeListing = field(repr=False)

    @property
    def monthly_priced(self) -> bool:
        return self.kind == ListingKind.OFFER and self.price > 0 and self.period == "month"


def rows_for(channel: HomeChannel, start: datetime, end: datetime, channels: list[HomeChannel] | None = None) -> list[Row]:
    """Listings of a city channel's metro whose post appeared in [start, end): base filter of §4, routed like publish."""
    channels = channels if channels is not None else list(HomeChannel.objects.filter(enabled=True).exclude(city=""))
    found = (
        HomeListing.objects.filter(
            country=channel.country,
            canonical__isnull=True,
            needs_review=False,
            status__in=BASE_STATUSES,
            post__status=PostStatus.ACCEPTED,
            post__posted_at__gte=start,
            post__posted_at__lt=end,
        )
        .select_related("post", "home_area")
        .annotate(photo_n=Count("post__media", filter=Q(post__media__status=MediaStatus.STORED)))
        .order_by("post__posted_at", "id")
    )
    out = []
    for listing in found:
        found_channel = route(listing, channels)
        if found_channel is None or found_channel.pk != channel.pk:
            continue
        area = safe_area(listing)
        out.append(
            Row(
                id=listing.pk,
                kind=listing.kind,
                type=listing.property_type,
                price=float(listing.price_amount or 0),
                currency=listing.price_currency or "",
                period=listing.price_period,
                mode=listing.rental_mode,
                basis=listing.price_basis,
                area=(listing.home_area.canonical if listing.home_area_id else area.split(",")[0].strip()),
                area_fa=(listing.home_area.fa if listing.home_area_id else ""),
                area_name=area.split(",")[0].strip(),
                text=listing.post.text or "",
                posted_at=listing.post.posted_at,
                photos=listing.photo_n,
                status=listing.status,
                listing=listing,
            )
        )
    return out


def _norm(text: str) -> str:
    return re.sub(r"[^\w]", "", text or "")


def dedupe(rows: list[Row], *, similar: bool = False) -> list[Row]:
    """One row per repost: same first 120 word characters and price. `similar` also folds text similarity > 0.8 at the same price."""
    seen, out = set(), []
    for row in sorted(rows, key=lambda r: (r.posted_at, r.id)):
        key = (_norm(row.text)[:120], row.price)
        if key in seen:
            continue
        if similar and any(
            other.price == row.price and SequenceMatcher(None, _norm(other.text)[:600], _norm(row.text)[:600]).ratio() > 0.8 for other in out
        ):
            continue
        seen.add(key)
        out.append(row)
    return out


def medians(rows: list[Row], currency: str) -> dict[str, tuple[int, float]]:
    """Monthly offer medians by type, n >= 8, p5–p95 trimmed when n >= 20. Same arithmetic as the hand-made v1."""
    out = {}
    for kind in TYPES:
        prices = sorted(r.price for r in rows if r.monthly_priced and r.type == kind and r.currency == currency and r.mode != "short_term")
        n = len(prices)
        if n < MIN_MEDIAN_N:
            continue
        if n >= TRIM_FROM_N:
            low, high = prices[int(0.05 * (n - 1))], prices[int(round(0.95 * (n - 1)))]
            prices = [p for p in prices if low <= p <= high]
        out[kind] = (n, float(statistics.median(prices)))
    return out


def guarded_type(row: Row, meds: dict[str, tuple[int, float]]) -> str | None:
    """The type the post text supports, or None when it is unclear (then it is never the deal)."""
    text = row.text
    room_word = bool(_ROOM_WORD.search(text))
    whole = bool(_WHOLE_PLACE.search(text))
    if row.type == "room":
        return "room" if (room_word or not _FLAT_WORDS.search(text)) else None
    if row.type in {"house", "apartment"} and _BASEMENT_WORD.search(text) and not room_word:
        return "basement"  # «بیسمنت آپارتمان یک خواب» is a basement unit, priced against basements
    if row.type in {"basement", "house", "apartment"} and room_word and not whole:
        # «اتاق در بیسمنت», «اتاق در خانه», a room in a shared flat: priced per room.
        room = meds.get("room")
        if room and row.price <= room[1] * 1.3:
            return "room"
        return None
    return row.type


def deal_of_day(new_rows: list[Row], meds: dict[str, tuple[int, float]], currency: str, country: str) -> tuple[Row, str, float] | None:
    """§2.2: an active monthly offer of the last 24 h, 15–60 % under its type median; photo first, then the largest discount."""
    best = None
    for row in new_rows:
        if not row.monthly_priced or row.status != ListingStatus.ACTIVE or row.currency != currency or row.mode == "short_term":
            continue
        if country == "DE" and rent_basis(row.listing) != "گرم":
            continue  # v1 lesson: only warm rent is compared; cold or unknown against a mixed median is not a fair deal
        kind = guarded_type(row, meds)
        if kind is None or kind not in meds:
            continue
        discount = 1 - row.price / meds[kind][1]
        if not DEAL_MIN <= discount <= DEAL_MAX:
            continue
        score = (row.photos > 0, discount)
        if best is None or score > best[0]:
            best = (score, row, kind, discount)
    return (best[1], best[2], best[3]) if best else None


def newest(new_rows: list[Row], currency: str) -> Row | None:
    """The newest priced monthly offer with a photo; without photos, the newest priced one."""
    priced = [r for r in new_rows if r.monthly_priced and r.currency == currency and r.status == ListingStatus.ACTIVE]
    with_photo = [r for r in priced if r.photos > 0]
    pool = with_photo or priced
    return max(pool, key=lambda r: (r.posted_at, r.id)) if pool else None


_CLEAN_AREA = re.compile(r"^[A-Za-zÀ-ÿ؀-ۿ][A-Za-zÀ-ÿ؀-ۿ .'\-]{1,28}$")


@dataclass
class Daily:
    channel: HomeChannel
    now: datetime
    n: int
    offer: int
    wanted: int
    by_type: list[tuple[str, int]]
    meds: dict[str, tuple[int, float]]
    tiles: list[tuple[str, float]]
    stats_meds: list[tuple[str, float]]
    busiest_area: tuple[str, int] | None
    photos: int
    price_range: tuple[float, float] | None
    long_term: int
    deal: tuple[Row, str, float] | None
    newest: Row | None
    ids: list[int]

    @property
    def skip(self) -> bool:
        return self.n < MIN_DAILY


def daily(channel: HomeChannel, now: datetime, channels: list[HomeChannel] | None = None) -> Daily:
    currency = CURRENCY.get(channel.country, "")
    month = dedupe(rows_for(channel, now - timedelta(days=MEDIAN_DAYS), now, channels))
    meds = medians(month, currency)
    new = dedupe([r for r in month if r.posted_at > now - timedelta(hours=24)], similar=True)
    offers = [r for r in new if r.kind == ListingKind.OFFER]
    wanted = sum(1 for r in new if r.kind == ListingKind.WANTED)
    by_count = sorted(meds.items(), key=lambda item: (-item[1][0], item[0]))
    tiles = [(kind, value[1]) for kind, value in by_count[:3]]
    stats_meds = sorted(((kind, value[1]) for kind, value in by_count[:4]), key=lambda item: item[1])
    areas = collections.Counter(r.area for r in new if r.area and _CLEAN_AREA.match(r.area) and r.area.casefold() != channel.city.casefold())
    top = areas.most_common(1)
    busiest = top[0] if top and top[0][1] >= 3 else None
    prices = sorted(
        r.price for r in offers if r.monthly_priced and r.currency == currency and (r.type not in meds or r.price >= 0.4 * meds[r.type][1])
    )
    return Daily(
        channel=channel,
        now=now,
        n=len(new),
        offer=len(offers),
        wanted=wanted,
        by_type=collections.Counter(r.type for r in offers).most_common(),
        meds=meds,
        tiles=tiles,
        stats_meds=stats_meds,
        busiest_area=busiest,
        photos=sum(1 for r in new if r.photos > 0),
        price_range=(prices[0], prices[-1]) if len(prices) >= 3 else None,
        long_term=sum(1 for r in new if r.mode == "long_term"),
        deal=deal_of_day(new, meds, currency, channel.country),
        newest=newest(new, currency),
        ids=[r.id for r in new],
    )
