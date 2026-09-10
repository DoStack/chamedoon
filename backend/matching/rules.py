from __future__ import annotations

from django.utils import timezone

from item_requests.models import ItemRequest, RequestStatus, RequestType


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


def dates_compatible(demand: ItemRequest, supply: ItemRequest) -> bool:
    desired = demand.desired_date or demand.date_from
    return supply.date_from <= desired <= supply.date_to


def capacity_sufficient(demand: ItemRequest, supply: ItemRequest) -> bool:
    if demand.weight_kg is None or supply.capacity_kg is None:
        return False
    return supply.capacity_kg >= demand.weight_kg


def exclusions_allow(demand: ItemRequest, supply: ItemRequest) -> bool:
    demand_codes = {category.code for category in demand.item_categories.all()}
    excluded_codes = {category.code for category in supply.excluded_categories.all()}
    return demand_codes.isdisjoint(excluded_codes)


def is_matchable(item_request: ItemRequest) -> bool:
    return item_request.status == RequestStatus.ACTIVE and not item_request.is_expired()


def both_matchable(demand: ItemRequest, supply: ItemRequest) -> bool:
    return (
        is_matchable(demand)
        and is_matchable(supply)
        and demand.user_id != supply.user_id
        and timezone.now() < demand.expires_at
        and timezone.now() < supply.expires_at
    )


def passes_hard_rules(demand: ItemRequest, supply: ItemRequest) -> bool:
    return (
        both_matchable(demand, supply)
        and origins_match(demand, supply)
        and destinations_match(demand, supply)
        and dates_compatible(demand, supply)
        and capacity_sufficient(demand, supply)
        and exclusions_allow(demand, supply)
    )
