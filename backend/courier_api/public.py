"""/api/v1/public/courier/: read-only courier board for a browser without an account (web mode).

What leaves here: the kind (trip or package), the route (origin and destination cities), the date, capacity or
weight, item categories and when it was posted. Nothing about the person: no name, username, phone, Telegram id,
poster id, free-text description, photo or source link. Contact stays behind sign-in and a two-sided introduction.

Every answer passes `PublicCardSerializer`, whose field list is the whole contract; a test fails if it grows.
Answers are cached for a minute (server and browser) and each address gets PUBLIC_PER_MINUTE calls a minute.
"""

from __future__ import annotations

import hashlib

from django.core.cache import cache
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from courier import conf, search, texts
from courier_api import serializers as s
from courier_api.errors import ApiError
from courier_api.views import SEARCH_CAP, V1View, _gone, _route_query, read_query
from home import conf as home_conf
from item_requests.locations import locations_payload
from item_requests.models import HIDDEN_POST_REASONS, Category, ItemRequest, RequestStatus
from web.session import client_ip

CACHE_SECONDS = 60
PUBLIC_PER_MINUTE = 120
CACHE_HEADER = f"public, max-age={CACHE_SECONDS}"
# The whole public shape of one request. Adding a field here is a privacy decision: tests pin this list.
PUBLIC_FIELDS = ("id", "type", "origin", "stops", "date", "date_kind", "capacity_kg", "weight_kg", "categories", "created_at")


class PublicThrottle(SimpleRateThrottle):
    """Per address, behind the proxy as the web login counts it."""

    scope = "courier_public"
    rate = f"{PUBLIC_PER_MINUTE}/min"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": client_ip(request) or "unknown"}


class PlaceOut(serializers.Serializer):
    country = serializers.CharField()
    city = serializers.CharField()
    name_fa = serializers.CharField()
    name_en = serializers.CharField()


class CategoryOut(serializers.Serializer):
    code = serializers.CharField()
    name_fa = serializers.CharField()
    name_en = serializers.CharField()


class PublicCardSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    type = serializers.CharField()
    origin = PlaceOut()
    stops = PlaceOut(many=True)
    date = serializers.DateField(allow_null=True)
    date_kind = serializers.CharField(allow_null=True)
    capacity_kg = serializers.DecimalField(max_digits=6, decimal_places=2, allow_null=True)
    weight_kg = serializers.DecimalField(max_digits=6, decimal_places=2, allow_null=True)
    categories = CategoryOut(many=True)
    created_at = serializers.DateTimeField()


class PublicListSerializer(serializers.Serializer):
    items = PublicCardSerializer(many=True)
    total = serializers.IntegerField()
    next_offset = serializers.IntegerField(allow_null=True)


class PublicListQuery(s.ListQuerySerializer):
    """The Mini App's list query without `ids` (a shared link opens the plain route list here)."""

    ids = None
    offset = serializers.IntegerField(required=False, default=0, min_value=0, max_value=SEARCH_CAP)


def public_card(item: ItemRequest, names: s.Names) -> dict:
    return {
        "id": item.pk,
        "type": item.type,
        "origin": names.place(item.origin_country, item.origin_city),
        "stops": [names.place(country, city) for country, city in item.destination_stop_pairs()],
        "date": search.request_date(item),
        "date_kind": s._date_kind(item),
        "capacity_kg": item.capacity_kg,
        "weight_kg": item.weight_kg,
        "categories": s._categories(item.item_categories.all()),
        "created_at": item.created_at,
    }


class PublicView(V1View):
    """Anonymous GET only. Closed when the courier app or web mode is off for everyone."""

    throttle_classes = [PublicThrottle]
    http_method_names = ["get", "head", "options"]

    def initial(self, request, *args, **kwargs) -> None:
        super().initial(request, *args, **kwargs)
        if not (conf.courier_v2_on() and home_conf.web_login_on()):
            raise ApiError("not_found")

    def cached(self, request, build, query: dict | None = None) -> Response:
        # Keyed on the validated query only, so unknown or repeated parameters never make a new cache entry.
        shape = request.path + "?" + "&".join(f"{name}={value}" for name, value in sorted((query or {}).items()))
        key = "courier:public:" + hashlib.sha256(shape.encode()).hexdigest()
        body = cache.get(key)
        if body is None:
            body = build()
            cache.set(key, body, CACHE_SECONDS)
        response = Response(body)
        response["Cache-Control"] = CACHE_HEADER
        response["X-Robots-Tag"] = "noindex"
        return response


class PublicRequestListView(PublicView):
    def get(self, request):
        query = read_query(PublicListQuery, request)
        route = _route_query(query)

        def build() -> dict:
            rows = search.route_search(**route, limit=SEARCH_CAP, only_reachable=True)
            start, end = query["offset"], query["offset"] + query["limit"]
            names = s.Names()
            data = {"items": [public_card(item, names) for item in rows[start:end]], "total": len(rows), "next_offset": end if end < len(rows) else None}
            return PublicListSerializer(data).data

        return self.cached(request, build, query)


class PublicFacetsView(PublicView):
    def get(self, request):
        query = read_query(s.RouteQuerySerializer, request)
        route = _route_query(query)
        return self.cached(request, lambda: s.FacetsSerializer(search.route_facets(**route, limit=SEARCH_CAP, only_reachable=True)).data, query)


class PublicRequestDetailView(PublicView):
    def get(self, request, pk: int):
        def build() -> dict:
            item = ItemRequest.objects.filter(pk=pk).select_related("user").prefetch_related("item_categories").first()
            # Only what the public list could show: a person's request, matchable, with a way to reach them.
            if item is None or item.unmatchable_reason in HIDDEN_POST_REASONS or not search.is_person_request(item) or not search.has_way_to_reach(item):
                raise ApiError("not_found")
            if item.status != RequestStatus.ACTIVE or item.is_expired() or s.date_passed(item):
                raise _gone(item)
            if not search.is_matchable(item):
                raise ApiError("not_found")
            return PublicCardSerializer(public_card(item, s.Names())).data

        return self.cached(request, build)


class PublicLocationsView(PublicView):
    def get(self, request):
        def build() -> dict:
            names = s.Names()
            frequent = [{"origin": names.place(r[0], r[1]), "destination": names.place(r[2], r[3])} for r in search.frequent_routes()]
            categories = Category.objects.filter(is_active=True)
            return s.LocationsSerializer(
                {
                    "countries": locations_payload(locale="fa"),
                    "frequent": frequent,
                    "categories": [{"code": row.code, "name_fa": row.name_fa, "name_en": row.name_en} for row in categories],
                }
            ).data

        return self.cached(request, build)


class PublicGuideView(PublicView):
    def get(self, request):
        from django.conf import settings

        from item_requests.models import CourierChannel

        def build() -> dict:
            channels = CourierChannel.objects.filter(enabled=True).exclude(username="")
            return s.GuideSerializer(
                {
                    "prohibited": texts.prohibited_items(),
                    "prohibited_footer": texts.PROHIBITED_FOOTER,
                    "main_channel": s.public_username(getattr(settings, "TELEGRAM_CHANNEL_USERNAME", "")),
                    "channels": [{"country": row.country, "username": row.username, "title": row.title} for row in channels],
                    "bot_username": conf.bot_username() or None,
                }
            ).data

        return self.cached(request, build)

