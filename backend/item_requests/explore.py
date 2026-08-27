from __future__ import annotations

from django.http import QueryDict
from django.utils import timezone
from django.utils.dateparse import parse_date

from item_requests.models import ItemRequest, RequestStatus, RequestType
from item_requests.services import expire_user_requests
from users.models import User

LIST_LIMIT = 100


def open_requests_queryset(user: User, params: QueryDict | dict | None = None):
    expire_user_requests(user)
    queryset = (
        ItemRequest.objects.filter(status=RequestStatus.ACTIVE, expires_at__gt=timezone.now())
        .exclude(user=user)
        .select_related("user")
        .prefetch_related("item_categories", "excluded_categories")
    )
    if params:
        queryset = apply_open_request_filters(queryset, params)
    return queryset.distinct()


def apply_open_request_filters(queryset, params: QueryDict | dict):
    get = params.get
    request_type = get("type")
    if request_type in RequestType.values:
        queryset = queryset.filter(type=request_type)

    origin_country = (get("origin_country") or "").strip().upper()
    if origin_country:
        queryset = queryset.filter(origin_country=origin_country)
    origin_city = (get("origin_city") or "").strip()
    if origin_city:
        queryset = queryset.filter(origin_city=origin_city)

    destination_country = (get("destination_country") or "").strip().upper()
    if destination_country:
        queryset = queryset.filter(destination_country=destination_country)
    destination_city = (get("destination_city") or "").strip()
    if destination_city:
        queryset = queryset.filter(destination_city=destination_city)

    category = (get("category") or "").strip()
    if category:
        queryset = queryset.filter(item_categories__code=category)

    date_from = parse_date(get("date_from") or "")
    if date_from:
        queryset = queryset.filter(date_to__gte=date_from)
    date_to = parse_date(get("date_to") or "")
    if date_to:
        queryset = queryset.filter(date_from__lte=date_to)

    return queryset
