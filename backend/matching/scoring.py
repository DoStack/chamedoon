from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from item_requests.models import ItemRequest
from matching.rules import (
    EARLY_FLIGHT_DAYS,
    capacity_sufficient,
    destinations_match,
    flight_offset_days,
    is_documents_only,
    origins_match,
)

# Route (origin + destination) is a hard rule, so a passing pair starts at 50.
# Date, capacity and category decide whether it reaches 80 (outreach) or stays 60-80 (review).
ORIGIN_WEIGHT = Decimal("25")
DESTINATION_WEIGHT = Decimal("25")
DATE_WEIGHT = Decimal("30")
CAPACITY_WEIGHT = Decimal("10")
CATEGORY_WEIGHT = Decimal("10")

# Full date points for a flight 0-3 days after the sender's date, none at 60 days.
DATE_FULL_DAYS = 3
DATE_ZERO_DAYS = 60
# A flight before the sender's date keeps at most this share of the date points.
EARLY_FLIGHT_MAX_SHARE = Decimal("0.6")
# A documents envelope riding with a traveler who did not list documents.
DOCUMENTS_FALLBACK_SHARE = Decimal("0.5")


def _points(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_score(demand: ItemRequest, supply: ItemRequest) -> Decimal:
    origin = ORIGIN_WEIGHT if origins_match(demand, supply) else Decimal("0")
    destination = DESTINATION_WEIGHT if destinations_match(demand, supply) else Decimal("0")
    total = (
        origin
        + destination
        + date_proximity_score(demand, supply)
        + capacity_score(demand, supply)
        + category_score(demand, supply)
    )
    if total < 0:
        total = Decimal("0")
    if total > 100:
        total = Decimal("100")
    return _points(total)


def date_proximity_score(demand: ItemRequest, supply: ItemRequest) -> Decimal:
    offset = flight_offset_days(demand, supply)
    if offset is None or offset < -EARLY_FLIGHT_DAYS:
        return Decimal("0.00")
    if offset < 0:
        # 1 day early: 60% of the points, 3 days early: 20%.
        share = EARLY_FLIGHT_MAX_SHARE * Decimal(EARLY_FLIGHT_DAYS + 1 + offset) / Decimal(EARLY_FLIGHT_DAYS)
        return _points(DATE_WEIGHT * share)
    if offset <= DATE_FULL_DAYS:
        return DATE_WEIGHT
    if offset >= DATE_ZERO_DAYS:
        return Decimal("0.00")
    share = Decimal(DATE_ZERO_DAYS - offset) / Decimal(DATE_ZERO_DAYS - DATE_FULL_DAYS)
    return _points(DATE_WEIGHT * share)


def capacity_score(demand: ItemRequest, supply: ItemRequest) -> Decimal:
    return CAPACITY_WEIGHT if capacity_sufficient(demand, supply) else Decimal("0.00")


def category_score(demand: ItemRequest, supply: ItemRequest) -> Decimal:
    demand_codes = {category.code for category in demand.item_categories.all()}
    if not demand_codes:
        return Decimal("0.00")
    supply_codes = {category.code for category in supply.item_categories.all()}
    if not supply_codes:
        return Decimal("0.00")
    overlap = demand_codes.intersection(supply_codes)
    if not overlap and is_documents_only(demand):
        return _points(CATEGORY_WEIGHT * DOCUMENTS_FALLBACK_SHARE)
    ratio = Decimal(len(overlap)) / Decimal(len(demand_codes))
    return _points(CATEGORY_WEIGHT * ratio)


def score_label(score: Decimal) -> str:
    if score >= 80:
        return "STRONG"
    if score >= 60:
        return "POSSIBLE"
    return "WEAK"
