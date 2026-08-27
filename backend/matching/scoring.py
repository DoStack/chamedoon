from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from item_requests.models import ItemRequest
from matching.rules import destinations_match, origins_match

ORIGIN_WEIGHT = Decimal("30")
DESTINATION_WEIGHT = Decimal("30")
DATE_WEIGHT = Decimal("20")
CAPACITY_WEIGHT = Decimal("10")
CATEGORY_WEIGHT = Decimal("10")


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
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def date_proximity_score(demand: ItemRequest, supply: ItemRequest) -> Decimal:
    overlap_start = max(demand.date_from, supply.date_from)
    overlap_end = min(demand.date_to, supply.date_to)
    overlap_days = (overlap_end - overlap_start).days + 1
    if overlap_days <= 0:
        return Decimal("0.00")

    supply_span = (supply.date_to - supply.date_from).days + 1
    if supply_span == 1:
        return DATE_WEIGHT

    demand_span = (demand.date_to - demand.date_from).days + 1
    ratio = Decimal(overlap_days) / Decimal(demand_span)
    return (DATE_WEIGHT * ratio).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def capacity_score(demand: ItemRequest, supply: ItemRequest) -> Decimal:
    if not demand.weight_kg or not supply.capacity_kg:
        return Decimal("0.00")
    fill = Decimal(demand.weight_kg) / Decimal(supply.capacity_kg)
    if fill > 1:
        fill = Decimal("1")
    return (CAPACITY_WEIGHT * fill).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def category_score(demand: ItemRequest, supply: ItemRequest) -> Decimal:
    demand_codes = {category.code for category in demand.item_categories.all()}
    if not demand_codes:
        return Decimal("0.00")
    supply_codes = {category.code for category in supply.item_categories.all()}
    if not supply_codes:
        return CATEGORY_WEIGHT
    overlap = demand_codes.intersection(supply_codes)
    ratio = Decimal(len(overlap)) / Decimal(len(demand_codes))
    return (CATEGORY_WEIGHT * ratio).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def score_label(score: Decimal) -> str:
    if score >= 80:
        return "STRONG"
    if score >= 60:
        return "POSSIBLE"
    return "WEAK"
