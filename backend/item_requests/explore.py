from __future__ import annotations

from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.db import connection
from django.db.models import Q
from django.http import QueryDict
from django.utils import timezone
from django.utils.dateparse import parse_date

from item_requests.models import ItemRequest, RequestStatus, RequestType
from item_requests.services import expire_due_requests, expire_user_requests
from users.models import User

LIST_LIMIT = 100


def open_requests_queryset(
    user: User,
    params: QueryDict | dict | None = None,
    *,
    include_own: bool = False,
):
    expire_due_requests(sync_channel=False)
    expire_user_requests(user)
    queryset = public_open_requests_queryset()
    if not include_own:
        queryset = queryset.exclude(user=user)
    if params:
        queryset = apply_open_request_filters(queryset, params)
    return queryset.distinct()


def public_open_requests_queryset():
    return (
        ItemRequest.objects.filter(status=RequestStatus.ACTIVE, expires_at__gt=timezone.now())
        .select_related("user")
        .prefetch_related("item_categories", "excluded_categories")
    )


def apply_open_request_filters(queryset, params: QueryDict | dict):
    filters = parse_explore_filters(params)
    if filters["type"] in RequestType.values:
        queryset = queryset.filter(type=filters["type"])
    if filters["ids"]:
        queryset = queryset.filter(pk__in=filters["ids"])

    if filters["origin_country"]:
        queryset = queryset.filter(origin_country=filters["origin_country"])
    if filters["origin_city"]:
        queryset = queryset.filter(origin_city=filters["origin_city"])

    dest_stops = filters["destination_stops"]
    if dest_stops:
        dest_q = Q()
        for country, city in dest_stops:
            dest_q |= _destination_q(country, city)
        queryset = queryset.filter(dest_q)
    elif filters["destination_country"] or filters["destination_city"]:
        queryset = queryset.filter(_destination_q(filters["destination_country"], filters["destination_city"]))

    if filters["categories"]:
        queryset = queryset.filter(item_categories__code__in=filters["categories"])

    date_from = parse_date(filters["date_from"])
    if date_from:
        queryset = queryset.filter(date_to__gte=date_from)
    date_to = parse_date(filters["date_to"])
    if date_to:
        queryset = queryset.filter(date_from__lte=date_to)
    flight_after = parse_date(filters["flight_after"])
    if flight_after:
        queryset = queryset.filter(
            Q(flight_date__gte=flight_after) | Q(flight_date__isnull=True, date_to__gte=flight_after)
        )
    weight = _parse_kg(filters["weight_kg"])
    if weight is not None:
        if filters["type"] == RequestType.DEMAND:
            queryset = queryset.filter(weight_kg__lte=weight)
        else:
            queryset = queryset.filter(capacity_kg__gte=weight)

    return queryset


def parse_explore_filters(params: QueryDict | dict | None = None) -> dict:
    params = params or {}
    request_type = (params.get("type") or "").strip().upper()
    if request_type not in RequestType.values:
        request_type = ""
    return {
        "type": request_type,
        "origin_country": (params.get("origin_country") or "").strip().upper(),
        "origin_city": (params.get("origin_city") or "").strip(),
        "destination_country": (params.get("destination_country") or "").strip().upper(),
        "destination_city": (params.get("destination_city") or "").strip(),
        "destination_stops": _destination_stops(params),
        "date_from": (params.get("date_from") or "").strip(),
        "date_to": (params.get("date_to") or "").strip(),
        "flight_after": (params.get("flight_after") or "").strip(),
        "weight_kg": (params.get("weight_kg") or "").strip(),
        "categories": _param_values(params, "category") or _list_values(params.get("categories") if params else None),
        "ids": _id_values(params.get("ids")),
    }


def explore_query(filters: dict) -> str:
    pairs: list[tuple[str, str]] = []
    if filters.get("type"):
        pairs.append(("type", filters["type"]))
    for key in (
        "origin_country",
        "origin_city",
        "destination_country",
        "destination_city",
        "date_from",
        "date_to",
        "flight_after",
        "weight_kg",
    ):
        value = (filters.get(key) or "").strip()
        if value:
            pairs.append((key, value))
    for stop in filters.get("destination_stops") or []:
        token = stop if isinstance(stop, str) else f"{stop[0]}:{stop[1]}"
        if token and ("destination", token) not in pairs:
            pairs.append(("destination", token))
    for code in filters.get("categories") or []:
        pairs.append(("category", code))
    if filters.get("ids"):
        pairs.append(("ids", ",".join(str(pk) for pk in filters["ids"])))
    return urlencode(pairs)


def filters_for_item_matches(item: ItemRequest) -> dict:
    origin = {"origin_country": item.origin_country, "origin_city": item.origin_city}
    stops = item.destination_stop_pairs()
    dest = {
        "destination_country": stops[0][0] if stops else item.destination_country,
        "destination_city": stops[0][1] if stops else item.destination_city,
        "destination_stops": [f"{country}:{city}" for country, city in stops],
    }
    categories = list(item.item_categories.values_list("code", flat=True))
    if item.type == RequestType.DEMAND:
        desired = item.desired_date or item.date_from
        return {
            "type": RequestType.SUPPLY,
            **origin,
            **dest,
            "date_from": "",
            "date_to": "",
            "flight_after": desired.isoformat() if desired else "",
            "weight_kg": str(item.weight_kg) if item.weight_kg is not None else "",
            "categories": categories,
        }
    flight = item.flight_date or item.date_to
    return {
        "type": RequestType.DEMAND,
        **origin,
        **dest,
        "date_from": "",
        "date_to": flight.isoformat() if flight else "",
        "flight_after": "",
        "weight_kg": str(item.capacity_kg) if item.capacity_kg is not None else "",
        "categories": categories,
    }


def _destination_stops(params) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []
    if hasattr(params, "getlist"):
        raw = list(params.getlist("destination"))
    else:
        value = params.get("destination") or params.get("destination_stops") if params else None
        raw = list(value) if isinstance(value, (list, tuple)) else [value] if value else []
    for item in raw:
        text = str(item or "").strip()
        if ":" not in text:
            continue
        country, city = text.split(":", 1)
        pair = (country.upper().strip(), city.strip())
        if pair[0] and pair[1] and pair not in seen:
            seen.append(pair)
    country = str((params.get("destination_country") if params else "") or "").upper().strip()
    city = str((params.get("destination_city") if params else "") or "").strip()
    if country and city and (country, city) not in seen:
        seen.insert(0, (country, city))
    return seen


def _destination_q(country: str, city: str) -> Q:
    country = (country or "").upper().strip()
    city = (city or "").strip()
    primary = Q()
    if country:
        primary &= Q(destination_country=country)
    if city:
        primary &= Q(destination_city=city)
    if country and city:
        return primary | _destination_cities_q(country, city)
    return primary


def _destination_cities_q(country: str, city: str) -> Q:
    if connection.vendor == "postgresql":
        return Q(destination_cities__contains=[{"country": country, "city": city}])
    return Q(destination_cities__icontains=f'"city": "{city}"') & Q(
        destination_cities__icontains=f'"country": "{country}"'
    )


def _parse_kg(value: str) -> Decimal | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        amount = Decimal(raw)
    except (InvalidOperation, TypeError):
        return None
    return amount if amount > 0 else None


def _id_values(value) -> list[int]:
    """`ids=12,15` pins the list to those requests (outreach links show exactly the matched travelers)."""
    parts = [part.strip() for part in str(value or "").split(",")]
    return [int(part) for part in parts if part.isdigit()][:20]


def _list_values(value) -> list[str]:
    raw = value if isinstance(value, (list, tuple)) else [value] if value else []
    seen: list[str] = []
    for item in raw:
        code = str(item or "").strip().upper()
        if code and code not in seen:
            seen.append(code)
    return seen


def _param_values(params, key: str) -> list[str]:
    if hasattr(params, "getlist"):
        return _list_values(params.getlist(key))
    return _list_values(params.get(key) if params else None)


def open_request_facets(user: User | None = None, request_type: str = "") -> dict:
    expire_due_requests(sync_channel=False)
    if user is not None:
        expire_user_requests(user)
    params = {"type": request_type} if request_type else None
    queryset = public_open_requests_queryset()
    if params:
        queryset = apply_open_request_filters(queryset, params)
    queryset = queryset.distinct()
    origins = list(
        queryset.order_by("origin_country", "origin_city")
        .values_list("origin_country", "origin_city")
        .distinct()
    )
    destinations = list(
        queryset.order_by("destination_country", "destination_city")
        .values_list("destination_country", "destination_city")
        .distinct()
    )
    category_codes = {
        code for code in queryset.values_list("item_categories__code", flat=True) if code
    }
    return {
        "origins": origins,
        "destinations": destinations,
        "category_codes": category_codes,
    }
