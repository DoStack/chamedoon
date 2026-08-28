from __future__ import annotations

from urllib.parse import urlencode

from django.http import QueryDict
from django.utils import timezone
from django.utils.dateparse import parse_date

from item_requests.models import ItemRequest, RequestStatus, RequestType
from item_requests.services import expire_user_requests
from users.models import User

LIST_LIMIT = 100


def open_requests_queryset(user: User, params: QueryDict | dict | None = None):
    expire_user_requests(user)
    queryset = public_open_requests_queryset().exclude(user=user)
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

    if filters["origin_country"]:
        queryset = queryset.filter(origin_country=filters["origin_country"])
    if filters["origin_city"]:
        queryset = queryset.filter(origin_city=filters["origin_city"])

    if filters["destination_country"]:
        queryset = queryset.filter(destination_country=filters["destination_country"])
    if filters["destination_city"]:
        queryset = queryset.filter(destination_city=filters["destination_city"])

    if filters["categories"]:
        queryset = queryset.filter(item_categories__code__in=filters["categories"])

    date_from = parse_date(filters["date_from"])
    if date_from:
        queryset = queryset.filter(date_to__gte=date_from)
    date_to = parse_date(filters["date_to"])
    if date_to:
        queryset = queryset.filter(date_from__lte=date_to)

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
        "date_from": (params.get("date_from") or "").strip(),
        "date_to": (params.get("date_to") or "").strip(),
        "categories": _param_values(params, "category"),
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
    ):
        value = (filters.get(key) or "").strip()
        if value:
            pairs.append((key, value))
    for code in filters.get("categories") or []:
        pairs.append(("category", code))
    return urlencode(pairs)


def _param_values(params, key: str) -> list[str]:
    if hasattr(params, "getlist"):
        raw = params.getlist(key)
    else:
        value = params.get(key) if params else None
        raw = value if isinstance(value, (list, tuple)) else [value] if value else []
    seen: list[str] = []
    for item in raw:
        code = str(item or "").strip().upper()
        if code and code not in seen:
            seen.append(code)
    return seen


def open_request_facets(user: User | None = None, request_type: str = "") -> dict:
    params = {"type": request_type} if request_type else None
    if user is None:
        queryset = public_open_requests_queryset()
        if params:
            queryset = apply_open_request_filters(queryset, params)
        queryset = queryset.distinct()
    else:
        queryset = open_requests_queryset(user, params)
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
