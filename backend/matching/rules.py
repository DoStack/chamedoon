from __future__ import annotations

from datetime import date

from django.utils import timezone

from item_requests.models import ItemRequest, RequestStatus, RequestType

# A flight this many days before the sender's date still works (the date is often a guess).
EARLY_FLIGHT_DAYS = 3
# A route naming this many places (a whole-country list counts once) is an agency, not a trip.
MAX_PERSON_DESTINATIONS = 5
# Two requests by one user on the same route within this many days are one request posted twice.
DUPLICATE_DATE_DAYS = 3
DOCUMENTS = "DOCUMENTS"


def pair_demand_supply(left: ItemRequest, right: ItemRequest) -> tuple[ItemRequest, ItemRequest]:
    if left.type == RequestType.DEMAND and right.type == RequestType.SUPPLY:
        return left, right
    if left.type == RequestType.SUPPLY and right.type == RequestType.DEMAND:
        return right, left
    raise ValueError("Matching requires one DEMAND and one SUPPLY request.")


def origins_match(demand: ItemRequest, supply: ItemRequest) -> bool:
    return (
        demand.origin_country == supply.origin_country
        and demand.origin_city == supply.origin_city
    )


def destinations_match(demand: ItemRequest, supply: ItemRequest) -> bool:
    demand_stops = set(demand.destination_stop_pairs())
    supply_stops = set(supply.destination_stop_pairs())
    return bool(demand_stops and supply_stops and demand_stops & supply_stops)


def demand_reference_date(demand: ItemRequest) -> date | None:
    """The sender's date, or None when they gave none (any upcoming flight works)."""
    return demand.desired_date


def supply_flight_date(supply: ItemRequest) -> date | None:
    return supply.flight_date or supply.date_to


def flight_offset_days(demand: ItemRequest, supply: ItemRequest) -> int | None:
    """Days from the sender's date to the flight (negative = flight is earlier)."""
    flight = supply_flight_date(supply)
    if not flight:
        return None
    desired = demand_reference_date(demand)
    if desired is None:
        return max((flight - timezone.localdate()).days, 0)
    return (flight - desired).days


def dates_compatible(demand: ItemRequest, supply: ItemRequest) -> bool:
    offset = flight_offset_days(demand, supply)
    return offset is not None and offset >= -EARLY_FLIGHT_DAYS


def capacity_sufficient(demand: ItemRequest, supply: ItemRequest) -> bool:
    if demand.weight_kg is None or supply.capacity_kg is None:
        return False
    return demand.weight_kg <= supply.capacity_kg


def _codes(categories) -> set[str]:
    return {category.code for category in categories.all()}


def exclusions_allow(demand: ItemRequest, supply: ItemRequest) -> bool:
    return _codes(demand.item_categories).isdisjoint(_codes(supply.excluded_categories))


def is_documents_only(demand: ItemRequest) -> bool:
    return _codes(demand.item_categories) == {DOCUMENTS}


def categories_compatible(demand: ItemRequest, supply: ItemRequest) -> bool:
    demand_codes = _codes(demand.item_categories)
    supply_codes = _codes(supply.item_categories)
    if not demand_codes or not supply_codes or not exclusions_allow(demand, supply):
        return False
    # A small envelope fits in any traveler's bag unless they refuse documents.
    return bool(demand_codes & supply_codes) or is_documents_only(demand)


def destination_place_count(item_request: ItemRequest) -> int:
    """Distinct places on the route; a whole-country list ("to Iran") counts as one."""
    from market.catalog import cities_for_country

    by_country: dict[str, set[str]] = {}
    for country, city in item_request.destination_stop_pairs():
        by_country.setdefault(country, set()).add(city)
    count = 0
    for country, cities in by_country.items():
        whole_country = {slug for _code, slug in cities_for_country(country)}
        if len(whole_country) > 1 and whole_country <= cities:
            count += 1 + len(cities - whole_country)
        else:
            count += len(cities)
    return count


def is_person_request(item_request: ItemRequest) -> bool:
    """Channel, bot, business accounts and agency-style routes are never offered as a match."""
    from matching.contact import is_person_user

    return (
        is_person_user(item_request.user)
        and destination_place_count(item_request) < MAX_PERSON_DESTINATIONS
    )


def is_matchable(item_request: ItemRequest) -> bool:
    if item_request.status != RequestStatus.ACTIVE or item_request.is_expired():
        return False
    if item_request.unmatchable_reason:
        return False
    if item_request.type == RequestType.SUPPLY and item_request.flight_date:
        return item_request.flight_date >= timezone.localdate()
    return True


def same_request_dates(left: ItemRequest, right: ItemRequest) -> bool:
    if left.type == RequestType.DEMAND:
        left_day, right_day = left.desired_date, right.desired_date
        if left_day is None or right_day is None:
            return True
    else:
        left_day, right_day = supply_flight_date(left), supply_flight_date(right)
        if left_day is None or right_day is None:
            return left_day == right_day
    return abs((left_day - right_day).days) <= DUPLICATE_DATE_DAYS


def duplicate_requests(item_request: ItemRequest, *, newer: bool) -> list[ItemRequest]:
    """Active requests by the same user that repeat this one (route, dates, kg, categories)."""
    order = {"pk__gt": item_request.pk} if newer else {"pk__lt": item_request.pk}
    candidates = ItemRequest.objects.filter(
        user_id=item_request.user_id,
        type=item_request.type,
        status=RequestStatus.ACTIVE,
        origin_country=item_request.origin_country,
        origin_city=item_request.origin_city,
        **order,
    )
    key = _request_key(item_request)
    return [
        other
        for other in candidates.prefetch_related("item_categories")
        if _request_key(other) == key and same_request_dates(item_request, other)
    ]


def _request_key(item_request: ItemRequest) -> tuple:
    return (
        frozenset(item_request.destination_stop_pairs()),
        item_request.kg_value(),
        frozenset(_codes(item_request.item_categories)),
    )


def is_superseded_duplicate(item_request: ItemRequest) -> bool:
    """True when the same user posted this request again later; only the newest copy matches."""
    if item_request.pk is None:
        return False
    return bool(duplicate_requests(item_request, newer=True))


def both_matchable(demand: ItemRequest, supply: ItemRequest) -> bool:
    return (
        is_matchable(demand)
        and is_matchable(supply)
        and demand.user_id != supply.user_id
        and timezone.now() < demand.expires_at
        and timezone.now() < supply.expires_at
        and is_person_request(demand)
        and is_person_request(supply)
    )


def passes_hard_rules(demand: ItemRequest, supply: ItemRequest) -> bool:
    return (
        both_matchable(demand, supply)
        and origins_match(demand, supply)
        and destinations_match(demand, supply)
        and dates_compatible(demand, supply)
        and capacity_sufficient(demand, supply)
        and categories_compatible(demand, supply)
        and not is_superseded_duplicate(demand)
        and not is_superseded_duplicate(supply)
    )
