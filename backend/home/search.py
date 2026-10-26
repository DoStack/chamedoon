"""Mini App housing search: the filters beyond city, type, budget and area, shared by the feed and «گوش به زنگ».

The feed filters rows with `search_q`; an alert saved from the feed keeps the same filters in
`HomeAlert.search` and `fits` checks a new listing against them in Python, with the same rules,
so a saved search only messages listings the feed would have shown.

Country fields: Canada has utilities included and roommate gender; Germany has Anmeldung
possible, warm rent («warm only» leaves out listings priced cold) and «no WBS needed» (the
review sets the `wgb` flag when a post asks for a Wohnberechtigungsschein). The move-in date
matches listings available within a week either side of it.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Q

from home.models import HomeListing

MOVE_WINDOW_DAYS = 7
TERMS = ("lease", "sublet", "lease_transfer", "short")
GENDERS = ("female", "male", "couple")
SORTS = ("new", "price")
# Toggle filters per country: name -> countries where the sheet offers it.
COUNTRY_TOGGLES = {"bills": {"CA"}, "warm": {"DE"}, "reg": {"DE"}, "nowbs": {"DE"}}
TOGGLES = ("furnished", "owner", "bills", "warm", "reg", "nowbs")
# Keys of HomeAlert.search, as the feed URL spells them.
SEARCH_KEYS = ("types", "min", "max", "areas", "term", "q", "gender", "move", "by", *TOGGLES)


def _wbs_ids():
    # Negating a JSON key lookup drops rows without the key, so WBS listings are excluded by id.
    return HomeListing.objects.filter(flags__wgb=True).values("pk")


def move_window(move: date) -> tuple[date, date]:
    return move - timedelta(days=MOVE_WINDOW_DAYS), move + timedelta(days=MOVE_WINDOW_DAYS)


def couple_q() -> Q:
    """«مناسب زوج»: says couples, or a whole place not limited to one gender nor to one person."""
    whole = ~Q(property_type="room") & ~Q(roommate_gender__in=["female", "male"]) & (
        Q(occupants_wanted__isnull=True) | ~Q(occupants_wanted=1)
    )
    return Q(roommate_gender="couple") | Q(flags__couples_ok=True) | whole


def search_q(filters: dict) -> Q:
    """The filters of this module as one Q (city, type, budget, area and rental mode stay in the feed)."""
    q = Q()
    if filters.get("furnished"):
        q &= Q(furnished__in=["yes", "partial"])
    if filters.get("owner"):
        q &= ~Q(lister_type="agent")
    if filters.get("bills"):
        q &= Q(bills_included="yes") | Q(price_basis="warm")
    if filters.get("warm"):
        q &= ~Q(price_basis="cold")
    if filters.get("reg"):
        q &= Q(registration="yes")
    if filters.get("nowbs"):
        q &= ~Q(pk__in=_wbs_ids())
    gender = filters.get("gender")
    if gender in {"female", "male"}:
        q &= Q(roommate_gender=gender)
    elif gender == "couple":
        q &= couple_q()
    move = filters.get("move")
    if move:
        start, end = move_window(move)
        q &= Q(available_from__gte=start, available_from__lte=end)
    if filters.get("by"):
        q &= Q(available_from__isnull=True) | Q(available_from__lte=filters["by"])
    return q


# ---------------------------------------------------------------------------
# Saved searches: the same filters, as JSON on the alert and checked in Python.


def to_search(filters: dict) -> dict:
    """The JSON kept on HomeAlert.search: only the filters that are set."""
    out: dict = {}
    for key in SEARCH_KEYS:
        value = filters.get(key)
        if not value:
            continue
        out[key] = value.isoformat() if isinstance(value, date) else value
    return out


def from_search(search: dict | None) -> dict:
    """Filters from HomeAlert.search, with dates as dates and lists as lists."""
    search = dict(search or {})
    out: dict = {key: False for key in TOGGLES}
    out.update({"types": [], "areas": [], "min": None, "max": None, "term": "", "q": "", "gender": "", "move": None, "by": None})
    for key in TOGGLES:
        out[key] = bool(search.get(key))
    out["types"] = [str(value) for value in search.get("types") or []]
    out["areas"] = [str(value) for value in search.get("areas") or []]
    for key in ("min", "max"):
        try:
            out[key] = int(search.get(key) or 0) or None
        except (TypeError, ValueError):
            out[key] = None
    out["term"] = search.get("term") if search.get("term") in TERMS else ""
    out["gender"] = search.get("gender") if search.get("gender") in GENDERS else ""
    out["q"] = str(search.get("q") or "")
    for key in ("move", "by"):
        try:
            out[key] = date.fromisoformat(search.get(key) or "")
        except (TypeError, ValueError):
            out[key] = None
    return out


def _text(listing: HomeListing) -> str:
    post_text = listing.post.text if listing.post_id else ""
    return " ".join(part for part in (listing.area, listing.city, listing.summary_fa, listing.summary_en, post_text) if part).casefold()


def fits(search: dict | None, listing: HomeListing) -> bool:
    """Does this listing pass the saved search's extra filters? The same rules as the feed."""
    from home.alerts import listing_term, monthly_price

    if not search:
        return True
    filters = from_search(search)
    flags = listing.flags or {}
    if filters["min"]:
        price = monthly_price(listing)
        if price is not None and price < Decimal(filters["min"]):
            return False
    term = filters["term"]
    if term == "lease_transfer" and listing.rental_mode != "lease_transfer":
        return False
    if term in {"lease", "sublet", "short"} and listing_term(listing) != term:
        return False
    if filters["furnished"] and listing.furnished not in {"yes", "partial"}:
        return False
    if filters["owner"] and listing.lister_type == "agent":
        return False
    if filters["bills"] and not (listing.bills_included == "yes" or listing.price_basis == "warm"):
        return False
    if filters["warm"] and listing.price_basis == "cold":
        return False
    if filters["reg"] and listing.registration != "yes":
        return False
    if filters["nowbs"] and flags.get("wgb") is True:
        return False
    gender = filters["gender"]
    if gender in {"female", "male"} and listing.roommate_gender != gender:
        return False
    if gender == "couple":
        whole = listing.property_type != "room" and listing.roommate_gender not in {"female", "male"} and listing.occupants_wanted != 1
        if not (listing.roommate_gender == "couple" or flags.get("couples_ok") is True or whole):
            return False
    if filters["move"]:
        start, end = move_window(filters["move"])
        if listing.available_from is None or not start <= listing.available_from <= end:
            return False
    if filters["by"] and listing.available_from is not None and listing.available_from > filters["by"]:
        return False
    if filters["q"] and filters["q"].casefold() not in _text(listing):
        return False
    return True


# ---------------------------------------------------------------------------
# Price histogram of the budget section.

HISTOGRAM_BINS = 10
HISTOGRAM_DAYS = 7
HISTOGRAM_LIMIT = 400
HISTOGRAM_MIN_ROWS = 5


def histogram(prices: list[Decimal], low: int | None, high: int | None) -> list[dict]:
    """Bars of listings per price band: [{"height": 0..100, "in": bool, "from": int, "to": int}].
    The top band stops at the 95th percentile so one luxury flat does not flatten the rest.
    Empty when there are too few prices to say anything."""
    values = sorted(int(value) for value in prices if value is not None and value > 0)
    if len(values) < HISTOGRAM_MIN_ROWS:
        return []
    bottom = values[0]
    top = values[min(len(values) - 1, int(len(values) * 0.95))]
    if top <= bottom:
        return []
    width = (top - bottom) / HISTOGRAM_BINS
    counts = [0] * HISTOGRAM_BINS
    for value in values:
        index = min(HISTOGRAM_BINS - 1, int((min(value, top) - bottom) / width))
        counts[index] += 1
    peak = max(counts) or 1
    bars = []
    for index, count in enumerate(counts):
        start = int(bottom + index * width)
        end = int(bottom + (index + 1) * width)
        inside = (low is None or end >= low) and (high is None or start <= high)
        bars.append({"height": max(4, round(count * 100 / peak)) if count else 0, "in": inside and bool(low or high), "from": start, "to": end, "count": count})
    return bars
