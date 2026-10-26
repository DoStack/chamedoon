"""Who is on my route: the existing matcher for a saved request, a route search for the Mini App.

Results come nearest date first. The trust line («✅ N ارسال موفق در چمدون») is shown only when
the person really has completed shipments (a COMPLETED match, confirmed by both sides).
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from courier import texts
from item_requests.models import ItemRequest, RequestStatus, RequestType
from matching.models import Match, MatchStatus
from matching.rules import is_matchable, is_person_request

DEFAULT_ROUTES = [
    ("CA", "toronto", "IR", "tehran"),
    ("IR", "tehran", "CA", "toronto"),
    ("IR", "tehran", "CA", "montreal"),
]
FREQUENT_DAYS = 90
SEARCH_LIMIT = 40


def opposite(role: str) -> str:
    return RequestType.SUPPLY if role == RequestType.DEMAND else RequestType.DEMAND


def frequent_routes(limit: int = 3) -> list[tuple[str, str, str, str]]:
    """The busiest routes of the last 90 days, then the defaults."""
    since = timezone.now() - timedelta(days=FREQUENT_DAYS)
    counts: Counter = Counter(
        ItemRequest.objects.filter(created_at__gte=since)
        .values_list("origin_country", "origin_city", "destination_country", "destination_city")
        .iterator()
    )
    routes = [route for route, _count in counts.most_common(limit)]
    for route in DEFAULT_ROUTES:
        if len(routes) >= limit:
            break
        if route not in routes:
            routes.append(route)
    return routes[:limit]


def request_date(item: ItemRequest) -> date | None:
    """The date people see: the flight, or the date the package must arrive by."""
    if item.type == RequestType.SUPPLY:
        return item.flight_date or item.date_to
    return item.desired_date or item.date_to


def completed_count(user) -> int:
    return (
        Match.objects.filter(status=MatchStatus.COMPLETED)
        .filter(Q(demand_request__user=user) | Q(supply_request__user=user))
        .count()
    )


def trust_line(user) -> str:
    count = completed_count(user)
    return f"✅ {texts.fa(count)} ارسال موفق در چمدون" if count else ""


def _within_window(demand: ItemRequest, supply: ItemRequest) -> bool:
    """A package wanted «by December» does not ride a January flight."""
    flight = supply.flight_date or supply.date_to
    if demand.desired_date is None and demand.date_to and flight:
        return flight <= demand.date_to
    return True


def _sort_key(item: ItemRequest, reference: date) -> tuple:
    day = request_date(item)
    distance = abs((day - reference).days) if day else 10_000
    return (distance, -(item.created_at.timestamp() if item.created_at else 0))


def counterparts(item_request: ItemRequest) -> list[ItemRequest]:
    """The other side for a saved request, from the existing matcher, nearest date first."""
    from matching.services import find_match_candidates

    candidates = find_match_candidates(item_request)
    found = []
    for candidate in candidates:
        other = candidate.supply if item_request.type == RequestType.DEMAND else candidate.demand
        if not _within_window(candidate.demand, candidate.supply):
            continue
        found.append(other)
    reference = item_request.desired_date or item_request.flight_date or timezone.localdate()
    return sorted(found, key=lambda other: _sort_key(other, reference))


def route_search(
    *,
    role: str,
    origin: tuple[str, str] | None,
    destination: tuple[str, str] | None,
    month: str = "",
    size: str = "",
    exclude_user=None,
) -> list[ItemRequest]:
    """Mini App: open requests of the other side on a route, nearest date first.

    role is the searcher's side (DEMAND has a package and sees travelers).
    """
    today = timezone.localdate()
    wanted = opposite(role)
    rows = ItemRequest.objects.filter(type=wanted, status=RequestStatus.ACTIVE, expires_at__gt=timezone.now())
    if origin:
        rows = rows.filter(origin_country=origin[0], origin_city=origin[1])
    if exclude_user is not None:
        rows = rows.exclude(user=exclude_user)
    rows = rows.select_related("user").prefetch_related("item_categories", "excluded_categories").order_by("-created_at")
    window = _month_window(month, today)
    found = []
    for item in rows[:400]:
        if destination and destination not in item.destination_stop_pairs():
            continue
        if not is_matchable(item) or not is_person_request(item):
            continue
        day = request_date(item)
        if day is not None and day < today:
            continue
        if window and (day is None or not window[0] <= day <= window[1]):
            continue
        if size and not _size_fits(item, size):
            continue
        found.append(item)
        if len(found) >= SEARCH_LIMIT:
            break
    reference = window[0] if window else today
    return sorted(found, key=lambda item: _sort_key(item, reference))


def _month_window(month: str, today: date) -> tuple[date, date] | None:
    for key, start, end in texts.upcoming_months(today, count=12):
        if key == month:
            return start, end
    return None


def _size_fits(item: ItemRequest, size: str) -> bool:
    if size not in texts.SIZES:
        return True
    _label, package_kg, _space_kg, documents = texts.SIZES[size]
    codes = {category.code for category in item.item_categories.all()}
    if item.type == RequestType.SUPPLY:
        # A traveler fits a package of this size: enough space and, for documents, not refusing them.
        excluded = {category.code for category in item.excluded_categories.all()}
        if documents:
            return "DOCUMENTS" not in excluded
        return item.capacity_kg is not None and item.capacity_kg >= package_kg
    # A package fits a traveler's free space of this size.
    if documents:
        return codes == {"DOCUMENTS"}
    _label, _package, space_kg, _docs = texts.SIZES[size]
    return item.weight_kg is not None and item.weight_kg <= Decimal(space_kg)


def card_lines(item: ItemRequest) -> list[str]:
    """Two or three lines about the other person's request for the bot's result list."""
    day = request_date(item)
    lines = []
    if item.type == RequestType.SUPPLY:
        lines.append(f"✈️ {texts.day_month(day)}" if day else "✈️ تاریخ پرواز را ننوشته")
        space = f"🧳 جا: {texts.kg_text(item.capacity_kg)}" if item.capacity_kg else "🧳 جا: نامشخص"
        lines.append(f"{space} · {texts.COST}")
        excluded = [category.name_fa for category in item.excluded_categories.all()]
        if excluded:
            lines.append(f"🚫 قبول نمی‌کند: {texts.esc('، '.join(excluded))}")
    else:
        if item.desired_date:
            lines.append(f"📅 {texts.day_month(item.desired_date)}")
        elif item.date_to:
            lines.append(f"📅 تا {texts.day_month(item.date_to)}")
        what = "، ".join(category.name_fa for category in item.item_categories.all())
        weight = texts.kg_text(item.weight_kg) or "وزن نامشخص"
        lines.append(f"⚖️ {weight}{' · ' + texts.esc(what) if what else ''} · {texts.COST}")
    stops = item.destination_stop_pairs()
    if len(stops) > 1:
        lines.append(f"🗺 {texts.esc(texts.request_route(item))}")
    footer = " · ".join(part for part in (f"🕒 {texts.relative_day(item.created_at)}", trust_line(item.user)) if part)
    lines.append(footer)
    return lines
