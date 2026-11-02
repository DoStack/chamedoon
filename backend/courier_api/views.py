"""/api/v1/courier/: the courier API of the new Mini App. Thin callers of courier, matching, item_requests, support.

Every error is `{code, detail}`. Everything but /meta/ and the exchange is behind the `courier_v2` flag.
No view holds domain logic and no view messages anyone unless the domain's own switch is on.
"""

from __future__ import annotations

import json
import re
from functools import wraps

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from rest_framework.views import APIView

from api.permissions import IsTelegramUser
from courier import alerts as alert_domain
from courier import conf, intros, posting, search, texts
from courier.models import CourierIdempotencyKey, CourierIntro, CourierUserPrefs, IntroStatus
from courier.telegram import execute
from courier_api import serializers as s
from courier_api.auth import CourierJWTAuthentication
from courier_api.errors import ApiError, api_exception_handler
from courier_api.schema import doc
from item_requests.locations import locations_payload, resolve_or_create_city
from item_requests.models import Category, CourierChannel, ItemRequest, RequestStatus, RequestType
from item_requests.services import (
    cancel_item_request,
    close_item_request,
    create_item_request,
    due_to_expire_queryset,
    expire_if_needed,
    request_is_due,
    update_item_request,
)
from item_requests.validation import assert_request_editable
from matching.completion import complete_match, rate_match
from matching.models import Match, MatchRating, MatchStatus, matches_for_user
from support.models import SupportTicket, TicketStatus
from support.services import add_user_message, close_ticket_by_user, create_ticket, tickets_for_user
from users.exceptions import TelegramAuthError
from users.services import is_blocked, upsert_telegram_user
from users.telegram import parse_and_validate_init_data
from users.tokens import issue_access_token

API_VERSION = 1
MIN_API_VERSION = 1
CLIENT_VERSION_HEADER = "X-Courier-Api-Version"
# route_search scans 400 rows at most; pages are slices of one sorted result.
SEARCH_CAP = 400
META_CACHE = "public, max-age=0, s-maxage=30"
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
SAVED_SCREENS = {"courier", "courier_alerts", "demand", "supply", "requests", "matches", "explore", "support"}


class ExchangeThrottle(AnonRateThrottle):
    rate = "30/min"


class ReadThrottle(UserRateThrottle):
    scope = "courier_read"
    rate = "240/min"


class WriteThrottle(UserRateThrottle):
    scope = "courier_write"
    rate = "60/min"


class V1View(APIView):
    """Coded errors, JSON only, not cached unless a view says so, and closed while maintenance is on."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]
    obeys_maintenance = True

    def get_exception_handler(self):
        return api_exception_handler

    def initial(self, request, *args, **kwargs) -> None:
        if self.obeys_maintenance and conf.maintenance_on():
            raise ApiError("maintenance")
        super().initial(request, *args, **kwargs)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        if "Cache-Control" not in response:
            response["Cache-Control"] = "no-store"
        return response


class MeBase(V1View):
    """A signed-in caller with the flag on and a bundle the API still serves."""

    authentication_classes = [CourierJWTAuthentication]
    permission_classes = [IsTelegramUser]
    throttle_classes = [ReadThrottle]

    def get_throttles(self):
        if self.request.method in {"GET", "HEAD", "OPTIONS"}:
            return [ReadThrottle()]
        return [WriteThrottle()]

    def initial(self, request, *args, **kwargs) -> None:
        super().initial(request, *args, **kwargs)
        if not conf.courier_v2_for(request.user.telegram_user_id):
            raise ApiError("flag_off")
        version = request.headers.get(CLIENT_VERSION_HEADER, "")
        if version.isdigit() and int(version) < MIN_API_VERSION:
            raise ApiError("version_mismatch")


def out(serializer_class, data: dict, status: int = 200) -> Response:
    """Through the serializer, so a shape that leaves the contract fails instead of leaking."""
    return Response(serializer_class(data).data, status=status)


def read_body(serializer_class, request) -> dict:
    body = serializer_class(data=request.data)
    body.is_valid(raise_exception=True)
    return body.validated_data


def read_query(serializer_class, request) -> dict:
    query = serializer_class(data=request.query_params)
    query.is_valid(raise_exception=True)
    return query.validated_data


def domain_fields(exc: ValidationError) -> dict[str, list[str]]:
    if hasattr(exc, "message_dict"):
        return {name: [str(message) for message in messages] for name, messages in exc.message_dict.items()}
    return {"non_field_errors": [str(message) for message in exc.messages]}


IDEMPOTENCY_PARAMETER = OpenApiParameter(
    "Idempotency-Key", str, OpenApiParameter.HEADER, required=False, description="8 to 64 of A-Z a-z 0-9 _ -. A retry with the same key gets the first answer."
)


def idempotent(handler):
    """A retry after a lost response gets the first success again. Only successes are kept."""

    @wraps(handler)
    def wrapped(view, request, *args, **kwargs):
        key = request.headers.get("Idempotency-Key", "")
        if not key:
            return handler(view, request, *args, **kwargs)
        if not _IDEMPOTENCY_KEY.fullmatch(key):
            raise ApiError("bad_request", "Idempotency-Key is malformed.")
        saved = CourierIdempotencyKey.objects.filter(user=request.user, key=key).first()
        if saved is not None:
            return Response(saved.body, status=saved.status_code)
        response = handler(view, request, *args, **kwargs)
        if 200 <= response.status_code < 300:
            body = json.loads(JSONRenderer().render(response.data))
            CourierIdempotencyKey.objects.get_or_create(user=request.user, key=key, defaults={"status_code": response.status_code, "body": body})
        return response

    return wrapped


def _place(value: str) -> tuple[str, str] | None:
    return s._clean_place(value)


class NotFoundView(V1View):
    obeys_maintenance = False

    @extend_schema(exclude=True)
    def get(self, request, *args, **kwargs):
        raise ApiError("not_found")

    post = patch = delete = get


class MetaView(V1View):
    obeys_maintenance = False

    @doc(me=False, responses={200: s.MetaSerializer}, operation_id="meta", description="Readable during maintenance. `courier_v2` here is the switch for everyone; the allowlist answers in the exchange.")
    def get(self, request):
        response = out(
            s.MetaSerializer,
            {"api_version": API_VERSION, "min_api_version": MIN_API_VERSION, "courier_v2": conf.courier_v2_on(), "maintenance": conf.maintenance_on()},
        )
        response["Cache-Control"] = META_CACHE
        return response


class ExchangeView(V1View):
    throttle_classes = [ExchangeThrottle]

    @doc(
        "auth_required",
        "auth_invalid",
        "session_expired",
        "user_blocked",
        "auth_unavailable",
        "bad_request",
        "validation_failed",
        "rate_limited",
        me=False,
        request=s.ExchangeRequestSerializer,
        responses={200: s.ExchangeSerializer},
        operation_id="auth_exchange",
        description="Trade initData for a token. Safe to retry. Empty initData means the app was opened outside Telegram.",
    )
    def post(self, request):
        init_data = (read_body(s.ExchangeRequestSerializer, request).get("init_data") or "").strip()
        if not init_data:
            raise ApiError("auth_required")
        try:
            identity = parse_and_validate_init_data(init_data)
        except TelegramAuthError as exc:
            raise ApiError(exc.code, exc.message) from exc
        if is_blocked(identity):
            raise ApiError("user_blocked")
        user = upsert_telegram_user(identity)
        return out(
            s.ExchangeSerializer,
            {
                "access_token": issue_access_token(user),
                "expires_in": int(getattr(settings, "JWT_ACCESS_TOKEN_HOURS", 24 * 30)) * 3600,
                "user": {"id": user.pk, "first_name": user.first_name},
                "courier_v2": conf.courier_v2_for(user.telegram_user_id),
            },
        )


def _resolve_outreach(user, token: str) -> dict:
    """Records the click, as the old route does, and says where the domain sends this person."""
    from outreach.services import open_outreach

    path = open_outreach(token, user)
    target = {"kind": "screen", "id": None, "ids": [], "screen": "courier", "read_only": False}
    if path == "/app/":
        raise ApiError("not_found")
    found = re.fullmatch(r"/app/(matches|requests|explore)/(\d+)/", path)
    if found:
        kind = {"matches": "match", "requests": "own_request", "explore": "request"}[found.group(1)]
        return {**target, "kind": kind, "id": int(found.group(2)), "screen": None, "read_only": kind == "request"}
    many = re.search(r"[?&]ids=([\d,]+)", path)
    if many:
        return {**target, "kind": "travelers", "ids": [int(pk) for pk in many.group(1).split(",")], "screen": None, "read_only": True}
    return {**target, "read_only": True}


class DeepLinkView(MeBase):
    @doc(
        "start_invalid",
        "not_found",
        "bad_request",
        "validation_failed",
        request=s.DeepLinkRequestSerializer,
        responses={200: s.DeepLinkSerializer},
        operation_id="deeplink",
        description="Where a startapp value lands. POST because an outreach link records its click.",
    )
    def post(self, request):
        return out(s.DeepLinkSerializer, self._resolve(read_body(s.DeepLinkRequestSerializer, request)["start"].strip(), request.user))

    def _resolve(self, start: str, user) -> dict:
        target = {"kind": "screen", "id": None, "ids": [], "screen": "courier", "read_only": False}
        if not start:
            return target
        if start in SAVED_SCREENS:
            return {**target, "screen": start}
        if start == "home" or start == "home_alerts" or start.startswith("home_"):
            return {**target, "kind": "housing", "screen": None}
        if start.startswith("o_") and re.fullmatch(r"[A-Za-z0-9_-]{8,32}", start[2:]):
            return _resolve_outreach(user, start[2:])
        found = re.fullmatch(r"(explore|request|match|ticket|intro)_(\d{1,18})", start)
        if found is None:
            raise ApiError("start_invalid")
        kind, pk = found.group(1), int(found.group(2))
        return {**target, **self._by_id(kind, pk, user), "id": pk, "screen": None}

    def _by_id(self, kind: str, pk: int, user) -> dict:
        if kind in {"explore", "request"}:
            item = ItemRequest.objects.filter(pk=pk).first()
            if item is not None and item.user_id == user.pk:
                return {"kind": "own_request"}
            # An old `request_<id>` link of someone else's request opens only while it is active.
            if item is not None and (kind == "explore" or item.status == RequestStatus.ACTIVE):
                return {"kind": "request"}
        elif kind == "match" and matches_for_user(user).filter(pk=pk).exists():
            return {"kind": "match"}
        elif kind == "ticket" and tickets_for_user(user).filter(pk=pk).exists():
            return {"kind": "ticket"}
        elif kind == "intro" and CourierIntro.objects.filter(Q(requester=user) | Q(target=user), pk=pk).exists():
            return {"kind": "intro"}
        raise ApiError("not_found")


class LocationsView(MeBase):
    @doc(responses={200: s.LocationsSerializer}, operation_id="locations", description="Countries and cities by usage, frequent routes, and the categories a request can have.")
    def get(self, request):
        names = s.Names()
        frequent = [
            {"origin": names.place(route[0], route[1]), "destination": names.place(route[2], route[3])}
            for route in search.frequent_routes()
        ]
        categories = Category.objects.filter(is_active=True)
        return out(
            s.LocationsSerializer,
            {
                "countries": locations_payload(locale=s.prefs_shape(request.user)["locale"] or "fa"),
                "frequent": frequent,
                "categories": [{"code": row.code, "name_fa": row.name_fa, "name_en": row.name_en} for row in categories],
            },
        )


class CitiesView(MeBase):
    @doc("city_invalid", "validation_failed", "bad_request", request=s.CityCreateSerializer, responses={200: s.CitySerializer}, operation_id="city_resolve", description="A typed city: the existing one, or a new one in an active country, as in the bot.")
    def post(self, request):
        body = read_body(s.CityCreateSerializer, request)
        city = resolve_or_create_city(body["country"], body["name"])
        if city is None:
            raise ApiError("city_invalid")
        return out(s.CitySerializer, {"slug": city.slug, "name_fa": city.name_fa, "name_en": city.name_en})


class GuideView(MeBase):
    @doc(responses={200: s.GuideSerializer}, operation_id="guide", description="The prohibited list, admin-editable, and the channels for the account sheet.")
    def get(self, request):
        channels = CourierChannel.objects.filter(enabled=True).exclude(username="")
        return out(
            s.GuideSerializer,
            {
                "prohibited": texts.prohibited_items(),
                "prohibited_footer": texts.PROHIBITED_FOOTER,
                "main_channel": s.public_username(getattr(settings, "TELEGRAM_CHANNEL_USERNAME", "")),
                "channels": [{"country": row.country, "username": row.username, "title": row.title} for row in channels],
                "bot_username": conf.bot_username() or None,
            },
        )


def _route_query(query: dict) -> dict:
    origin, destination = _place(query["origin"]), _place(query["destination"])
    if origin and origin == destination:
        raise ApiError("same_city")
    return {"role": query["side"], "origin": origin, "destination": destination, "month": query["month"], "size": query["size"]}


class RequestListView(MeBase):
    @doc(
        "same_city",
        "validation_failed",
        parameters=[s.ListQuerySerializer],
        responses={200: s.RequestListSerializer},
        operation_id="requests_list",
        description="The other side's open requests on a route, nearest date first, without the caller's own. `next_offset` is null on the last page.",
    )
    def get(self, request):
        query = read_query(s.ListQuerySerializer, request)
        if query["ids"]:
            rows = self._by_ids(query["ids"], request.user)
        else:
            rows = search.route_search(**_route_query(query), exclude_user=request.user, limit=SEARCH_CAP, only_reachable=True)
        start, limit = query["offset"], query["limit"]
        end = start + limit
        return out(
            s.RequestListSerializer,
            {"items": s.cards(rows[start:end]), "total": len(rows), "next_offset": end if end < len(rows) else None},
        )

    def _by_ids(self, ids: str, user) -> list[ItemRequest]:
        wanted = [int(part) for part in ids.split(",") if part.strip().isdigit()][:40]
        rows = (
            ItemRequest.objects.filter(pk__in=wanted, status=RequestStatus.ACTIVE)
            .exclude(user=user)
            .prefetch_related("item_categories", "excluded_categories")
        )
        return sorted(rows, key=lambda item: wanted.index(item.pk))


class FacetsView(MeBase):
    @doc("same_city", "validation_failed", parameters=[s.RouteQuerySerializer], responses={200: s.FacetsSerializer}, operation_id="requests_facets", description="Live counts for the filter sheet. Each section is counted from the other section's choice.")
    def get(self, request):
        query = read_query(s.RouteQuerySerializer, request)
        return out(s.FacetsSerializer, search.route_facets(**_route_query(query), exclude_user=request.user, limit=SEARCH_CAP, only_reachable=True))


def _gone(item: ItemRequest) -> ApiError:
    code = "request_date_passed" if s.date_passed(item) else "request_inactive"
    return ApiError(code, route=s.route_keys(item))


def _open_request(pk: int, user) -> ItemRequest:
    """Someone else's request that is still open, or the error that says why not."""
    item = (
        ItemRequest.objects.filter(pk=pk)
        .select_related("user")
        .prefetch_related("item_categories", "excluded_categories")
        .first()
    )
    if item is None:
        raise ApiError("not_found")
    if item.user_id == user.pk:
        raise ApiError("own_request", request_id=item.pk)
    if item.status != RequestStatus.ACTIVE or item.is_expired() or s.date_passed(item):
        raise _gone(item)
    return item


class RequestDetailView(MeBase):
    @doc(
        "not_found",
        "own_request",
        "request_inactive",
        "request_date_passed",
        responses={200: s.RequestDetailSerializer},
        operation_id="request_detail",
        description="One open request. No name, username or source link. `reach` says what the main button does before the tap. Handles and links typed into free text are masked.",
    )
    def get(self, request, pk: int):
        return out(s.RequestDetailSerializer, s.request_detail(_open_request(pk, request.user), request.user, s.Names()))


class IntroCreateView(MeBase):
    @doc(
        "not_found",
        "own_request",
        "request_inactive",
        "request_date_passed",
        "intro_closed",
        "intro_unreachable",
        "intro_no_public_contact",
        "intro_undelivered",
        "intro_cap_self",
        "intro_cap_target",
        "intros_off",
        request=None,
        responses={200: s.IntroSerializer, 201: s.IntroSerializer},
        operation_id="intro_create",
        served="`intro_undelivered` from PR 2.",
        description="Ask for an intro. 201 sent; 200 already asked, safe to retry: one intro per person and request. Creates no request and no channel post.",
    )
    def post(self, request, pk: int):
        item = _open_request(pk, request.user)
        started = intros.start_intro(request.user, item)
        if started.code in {"intro_accepted", "intro_pending"}:
            return out(s.IntroSerializer, s.intro_shape(started.intro, request.user, s.Names()))
        if started.code == "intro_sent":
            execute(started.others)
            return out(s.IntroSerializer, s.intro_shape(started.intro, request.user, s.Names()), status=201)
        if started.code == "request_inactive":
            raise _gone(item)
        if started.code == "own_request":
            raise ApiError("own_request", request_id=item.pk)
        if started.code == "intro_unreachable":
            raise ApiError("intro_unreachable" if search.has_way_to_reach(item) else "intro_no_public_contact")
        raise ApiError(started.code)


def _my_intro(pk: int, user) -> CourierIntro:
    intro = intros.intros_for(user).filter(pk=pk).first()
    if intro is None:
        raise ApiError("not_found")
    return intro


class IntroView(MeBase):
    @doc("not_found", responses={200: s.IntroSerializer}, operation_id="intro_detail", description="One intro the caller is part of. `contact` is set only after acceptance.")
    def get(self, request, pk: int):
        return out(s.IntroSerializer, s.intro_shape(_my_intro(pk, request.user), request.user, s.Names()))


class IntroRespondView(MeBase):
    @doc(
        "not_found",
        "intro_expired",
        "request_inactive",
        "request_date_passed",
        "bad_request",
        "validation_failed",
        request=s.IntroResponseSerializer,
        responses={200: s.IntroSerializer},
        operation_id="intro_respond",
        description="The target's answer. Answering twice returns the first answer.",
    )
    def post(self, request, pk: int):
        accept = read_body(s.IntroResponseSerializer, request)["accept"]
        intros.expire_old()
        intro = _my_intro(pk, request.user)
        if intro.target_id != request.user.pk:
            raise ApiError("not_found")
        if intro.status == IntroStatus.EXPIRED:
            raise ApiError("intro_expired")
        if intro.status == IntroStatus.PENDING:
            target_request = intro.target_request
            if target_request.status != RequestStatus.ACTIVE or target_request.is_expired() or s.date_passed(target_request):
                raise _gone(target_request)
            reply = intros.respond(intro.pk, request.user, accept=accept)
            execute(reply.others)
        return out(s.IntroSerializer, s.intro_shape(_my_intro(pk, request.user), request.user, s.Names()))


class MeView(MeBase):
    @doc(responses={200: s.MeSerializer}, operation_id="me", description="The account screen. `counts` is null when it cannot be read; the rest of the screen stands.")
    def get(self, request):
        user = request.user
        try:
            counts = {
                "active_requests": ItemRequest.objects.filter(user=user, status=RequestStatus.ACTIVE).count() - due_to_expire_queryset(user=user).count(),
                "open_matches": sum(1 for match in _open_matches(user) if intros.contact_released(match)),
                "pending_intros": sum(
                    1 for intro in intros.intros_for(user).filter(target=user, status=IntroStatus.PENDING) if intros.live_intro_status(intro) == IntroStatus.PENDING
                ),
                "alerts": alert_domain.user_alerts(user.telegram_user_id).count(),
            }
        except DatabaseError:
            counts = None
        return out(s.MeSerializer, {"id": user.pk, "first_name": user.first_name, "counts": counts, "prefs": s.prefs_shape(user)})


class PrefsView(MeBase):
    @doc(responses={200: s.PrefsSerializer}, operation_id="prefs_read", description="Defaults when nothing is saved. A read never writes.")
    def get(self, request):
        return out(s.PrefsSerializer, s.prefs_shape(request.user))

    @doc("validation_failed", "bad_request", request=s.PrefsSerializer, responses={200: s.PrefsSerializer}, operation_id="prefs_update", description="Only the fields sent change.")
    def patch(self, request):
        body = read_body(s.PrefsSerializer, request)
        CourierUserPrefs.objects.update_or_create(user=request.user, defaults=body)
        return out(s.PrefsSerializer, s.prefs_shape(request.user))


def _open_matches(user):
    return matches_for_user(user).filter(status=MatchStatus.CONNECTED).select_related("demand_request", "supply_request")


def _counterparts(item: ItemRequest) -> list[ItemRequest]:
    return [row for row in search.counterparts(item, expire=False) if search.has_way_to_reach(row)]


def _is_open(item: ItemRequest) -> bool:
    return item.status == RequestStatus.ACTIVE and not request_is_due(item)


def _with_suggestions(item: ItemRequest) -> int | None:
    if not _is_open(item):
        return None
    try:
        return len(_counterparts(item))
    except Exception:  # A failed count never takes down the screen around it.
        return None


def _my_item(pk: int, user) -> ItemRequest:
    item = ItemRequest.objects.filter(pk=pk, user=user).prefetch_related("item_categories", "excluded_categories").first()
    if item is None:
        raise ApiError("not_found")
    return item


def _post_or_refuse(request_type: str, form: dict) -> dict:
    try:
        return posting.build_payload(request_type, form)
    except posting.PostingError as exc:
        raise ApiError(exc.code, fields={exc.field: [exc.code]}) from exc


class MyRequestsView(MeBase):
    @doc("validation_failed", parameters=[s.StateQuerySerializer], responses={200: s.MyRequestsSerializer}, operation_id="my_requests", description="The caller's active requests, or the closed, cancelled, completed and expired ones.")
    def get(self, request):
        state = read_query(s.StateQuerySerializer, request)["state"]
        rows = ItemRequest.objects.filter(user=request.user).prefetch_related("item_categories", "excluded_categories")
        rows = [item for item in rows[:200] if _is_open(item) == (state == "active")][:100]
        names = s.Names()
        return out(s.MyRequestsSerializer, {"items": [s.my_request(item, names, suggestions=_with_suggestions(item)) for item in rows]})

    @doc(
        "validation_failed",
        "bad_request",
        "same_city",
        "date_invalid",
        "date_past",
        "flight_date_required",
        "category_required",
        "stops_one_country",
        "server_rejected",
        "duplicate_request",
        parameters=[IDEMPOTENCY_PARAMETER],
        request=s.RequestCreateSerializer,
        responses={201: s.MyRequestSerializer},
        operation_id="my_request_create",
        description="Post a package or a trip. Nobody is matched or told by this call; C09 shows the other side live. While `courier_v2` is on only for the allowlist, the post stays private to its author: no channel, no alert, no matching.",
    )
    @idempotent
    def post(self, request):
        form = read_body(s.RequestCreateSerializer, request)
        payload = _post_or_refuse(form["type"], form)
        duplicate = posting.find_duplicate(request.user, payload)
        if duplicate is not None:
            raise ApiError("duplicate_request", request_id=duplicate.pk)
        try:
            live = conf.courier_v2_on()
            item = create_item_request(request.user, payload, sync_channel=live, sync_matches=False, test_post=not live)
        except ValidationError as exc:
            raise ApiError("server_rejected", fields=domain_fields(exc)) from exc
        item = _my_item(item.pk, request.user)
        return out(s.MyRequestSerializer, s.my_request(item, s.Names()), status=201)


class MyRequestView(MeBase):
    @doc("not_found", responses={200: s.MyRequestSerializer}, operation_id="my_request", description="One of the caller's requests. No contact and no contact button. `date_passed` is C12.10.")
    def get(self, request, pk: int):
        item = _my_item(pk, request.user)
        return out(s.MyRequestSerializer, s.my_request(item, s.Names(), suggestions=_with_suggestions(item)))

    @doc(
        "not_found",
        "not_editable",
        "validation_failed",
        "bad_request",
        "same_city",
        "date_invalid",
        "date_past",
        "flight_date_required",
        "category_required",
        "stops_one_country",
        "server_rejected",
        request=s.RequestFormSerializer,
        responses={200: s.MyRequestSerializer},
        operation_id="my_request_update",
        description="Replace the form of an active request. The type stays.",
    )
    def patch(self, request, pk: int):
        item = _my_item(pk, request.user)
        form = read_body(s.RequestFormSerializer, request)
        payload = _post_or_refuse(item.type, form)
        try:
            with transaction.atomic():
                expire_if_needed(item)
                assert_request_editable(item)
        except ValidationError as exc:
            raise ApiError("not_editable") from exc
        try:
            update_item_request(item, payload, sync_matches=False)
        except ValidationError as exc:
            raise ApiError("server_rejected", fields=domain_fields(exc)) from exc
        return out(s.MyRequestSerializer, s.my_request(_my_item(pk, request.user), s.Names()))


class CounterpartsView(MeBase):
    @doc("not_found", "matches_failed", responses={200: s.CounterpartsSerializer}, operation_id="my_request_counterparts", description="The other side for a saved request, nearest date first. Suggestions only: no contact, nobody is told.")
    def get(self, request, pk: int):
        item = _my_item(pk, request.user)
        if not _is_open(item):
            return out(s.CounterpartsSerializer, {"items": []})
        try:
            rows = _counterparts(item)
        except Exception as exc:
            raise ApiError("matches_failed") from exc
        return out(s.CounterpartsSerializer, {"items": s.cards(rows[:40])})


class CloseRequestView(MeBase):
    @doc(
        "not_found",
        "not_editable",
        "validation_failed",
        "bad_request",
        request=s.CloseRequestSerializer,
        responses={200: s.MyRequestSerializer},
        operation_id="my_request_close",
        description="A trip closes, a package cancels. Open matches expire and the channel post is marked. Closing a closed request is `not_editable`.",
    )
    def post(self, request, pk: int):
        item = _my_item(pk, request.user)
        package_sent = read_body(s.CloseRequestSerializer, request)["package_sent"]
        try:
            if item.type == RequestType.SUPPLY:
                close_item_request(item, package_sent=package_sent)
            else:
                cancel_item_request(item)
        except ValidationError as exc:
            raise ApiError("not_editable") from exc
        return out(s.MyRequestSerializer, s.my_request(_my_item(pk, request.user), s.Names()))


def _my_matches(user):
    return (
        matches_for_user(user)
        .select_related("demand_request", "supply_request", "demand_request__user", "supply_request__user")
        .prefetch_related(
            "ratings",
            "demand_request__item_categories",
            "demand_request__excluded_categories",
            "supply_request__item_categories",
            "supply_request__excluded_categories",
        )
    )


class InboxView(MeBase):
    @doc("validation_failed", parameters=[s.StateQuerySerializer], responses={200: s.InboxSerializer}, operation_id="inbox", description="Incoming and outgoing intros and everyone connected, or the archive of declined, expired and finished.")
    def get(self, request):
        state = read_query(s.StateQuerySerializer, request)["state"]
        user = request.user
        names = s.Names()
        rows = list(intros.intros_for(user)[:100])

        def shaped(condition) -> list[dict]:
            return [s.intro_shape(intro, user, names) for intro in rows if condition(intros.live_intro_status(intro), intro)]

        wanted = {MatchStatus.CONNECTED} if state == "active" else {MatchStatus.COMPLETED, MatchStatus.EXPIRED}
        matches = [
            s.match_shape(match, user, names)
            for match in _my_matches(user).order_by("-updated_at")[:200]
            if s.live_match_status(match) in wanted and intros.contact_released(match)
        ][:100]
        data = {"incoming": [], "outgoing": [], "accepted": [], "closed": [], "matches": matches}
        if state == "active":
            data["incoming"] = shaped(lambda status, intro: status == IntroStatus.PENDING and intro.target_id == user.pk)
            data["outgoing"] = shaped(lambda status, intro: status == IntroStatus.PENDING and intro.requester_id == user.pk)
            data["accepted"] = shaped(lambda status, intro: status == IntroStatus.ACCEPTED)
        else:
            data["closed"] = shaped(lambda status, intro: status in {IntroStatus.DECLINED, IntroStatus.EXPIRED})
        return out(s.InboxSerializer, data)


def _my_match(pk: int, user) -> Match:
    match = _my_matches(user).filter(pk=pk).first()
    if match is None:
        raise ApiError("not_found")
    return match


class MatchView(MeBase):
    @doc(
        "not_found",
        "match_expired",
        "request_inactive",
        "contact_not_accepted",
        responses={200: s.MatchSerializer},
        operation_id="match_detail",
        description="A match the caller is part of. Contact only after a two-sided intro, or for a match made before `courier_intro_everywhere` went on. A closed other side is `request_inactive`, an expired match `match_expired`.",
    )
    def get(self, request, pk: int):
        match = _my_match(pk, request.user)
        other = match.counterpart_request(request.user)
        keys = s.route_keys(other)
        if s.live_match_status(match) == MatchStatus.EXPIRED:
            if other.status in {RequestStatus.CLOSED, RequestStatus.CANCELLED, RequestStatus.COMPLETED}:
                raise ApiError("request_inactive", route=keys)
            raise ApiError("match_expired", route=keys)
        if not intros.contact_released(match):
            found = intros.pair_intro(match)
            raise ApiError("contact_not_accepted", intro_id=found.pk if found else None)
        shape = s.match_shape(match, request.user, s.Names())
        if match.status == MatchStatus.CONNECTED and shape["contact"] is None:
            raise ApiError("request_inactive", route=keys)
        return out(s.MatchSerializer, shape)


class CompleteMatchView(MeBase):
    @doc("not_found", "match_not_open", request=None, responses={200: s.MatchSerializer}, operation_id="match_complete", description="Mark the order finished. Finishing twice returns the same match.")
    def post(self, request, pk: int):
        match = _my_match(pk, request.user)
        if not intros.contact_released(match) or s.live_match_status(match) == MatchStatus.EXPIRED:
            raise ApiError("match_not_open")
        try:
            complete_match(match, request.user)
        except ValidationError as exc:
            raise ApiError("match_not_open") from exc
        return out(s.MatchSerializer, s.match_shape(_my_match(pk, request.user), request.user, s.Names()))


class RateMatchView(MeBase):
    @doc(
        "not_found",
        "match_not_open",
        "already_rated",
        "rating_invalid",
        "validation_failed",
        "bad_request",
        request=s.RateSerializer,
        responses={201: s.MatchSerializer},
        operation_id="match_rate",
        description="Rate a finished order once. A score of 3 or more goes to the channel with its comment, per `notifications.channel`.",
    )
    def post(self, request, pk: int):
        body = read_body(s.RateSerializer, request)
        match = _my_match(pk, request.user)
        if match.status != MatchStatus.COMPLETED:
            raise ApiError("match_not_open")
        if MatchRating.objects.filter(match=match, rater=request.user).exists():
            raise ApiError("already_rated")
        try:
            rate_match(match, request.user, body["score"], body.get("comment", ""))
        except ValidationError as exc:
            raise ApiError("rating_invalid", fields=domain_fields(exc)) from exc
        except IntegrityError as exc:
            raise ApiError("already_rated") from exc
        return out(s.MatchSerializer, s.match_shape(_my_match(pk, request.user), request.user, s.Names()), status=201)


def _alert_window(month: str, size: str, side: str) -> dict:
    window = next(((start, end) for key, start, end in texts.upcoming_months() if key == month), None)
    weight = None
    if size:
        _label, package_kg, space_kg, _docs = texts.SIZES[size]
        weight = package_kg if side == RequestType.DEMAND else space_kg
    return {"date_from": window[0] if window else None, "date_to": window[1] if window else None, "weight_kg": weight}


def _alert_route(origin_key: str, destination_key: str) -> tuple[tuple[str, str], tuple[str, str]]:
    origin, destination = _place(origin_key), _place(destination_key)
    if not origin or not destination:
        raise ApiError("route_required")
    if origin == destination:
        raise ApiError("same_city")
    return origin, destination


class AlertsView(MeBase):
    @doc(responses={200: s.AlertsSerializer}, operation_id="alerts_list", description="The caller's alerts and the cap. `sending_on` false means alerts save but nothing is sent yet.")
    def get(self, request):
        return out(s.AlertsSerializer, s.alerts_shape(request.user.telegram_user_id))

    @doc(
        "route_required",
        "same_city",
        "alert_cap",
        "validation_failed",
        "bad_request",
        request=s.AlertCreateSerializer,
        responses={200: s.AlertSavedSerializer, 201: s.AlertSavedSerializer},
        operation_id="alert_create",
        description="201 new. 200: the same route and side was updated and switched back on, so a retry is safe.",
    )
    def post(self, request):
        body = read_body(s.AlertCreateSerializer, request)
        origin, destination = _alert_route(body["origin"], body["destination"])
        user = request.user
        try:
            alert, created = alert_domain.create_alert(
                telegram_user_id=user.telegram_user_id,
                chat_id=user.telegram_user_id,
                user=user,
                role=body["side"],
                origin=origin,
                destination=destination,
                **_alert_window(body["month"], body["size"], body["side"]),
            )
        except alert_domain.AlertCapError as exc:
            raise ApiError("alert_cap") from exc
        data = {"alert": s.alert_shape(alert, s.Names()), "created": created, "sending_on": conf.alerts_enabled()}
        return out(s.AlertSavedSerializer, data, status=201 if created else 200)


class AlertView(MeBase):
    def _alert(self, request, pk: int):
        alert = alert_domain.user_alerts(request.user.telegram_user_id).filter(pk=pk).first()
        if alert is None:
            raise ApiError("alert_not_found")
        return alert

    @doc(
        "alert_not_found",
        "alert_exists",
        "route_required",
        "same_city",
        "validation_failed",
        "bad_request",
        request=s.AlertUpdateSerializer,
        responses={200: s.AlertSavedSerializer},
        operation_id="alert_update",
        description="Pause or resume with `active`. Sending `origin` and `destination` edits the alert.",
    )
    def patch(self, request, pk: int):
        alert = self._alert(request, pk)
        body = read_body(s.AlertUpdateSerializer, request)
        if "origin" in body or "destination" in body:
            origin, destination = _alert_route(body.get("origin", ""), body.get("destination", ""))
            try:
                alert_domain.update_alert(alert, origin=origin, destination=destination, **_alert_window(body.get("month", ""), body.get("size", ""), alert.role))
            except alert_domain.AlertExistsError as exc:
                raise ApiError("alert_exists") from exc
        if "active" in body:
            alert.active = body["active"]
            alert.save(update_fields=["active"])
        return out(s.AlertSavedSerializer, {"alert": s.alert_shape(alert, s.Names()), "created": False, "sending_on": conf.alerts_enabled()})

    @doc("alert_not_found", responses={204: None}, operation_id="alert_delete", description="Delete the alert. A second delete is `alert_not_found`.")
    def delete(self, request, pk: int):
        self._alert(request, pk).delete()
        return Response(status=204)


def _my_ticket(pk: int, user) -> SupportTicket:
    ticket = tickets_for_user(user).filter(pk=pk).first()
    if ticket is None:
        raise ApiError("not_found")
    return ticket


class TicketsView(MeBase):
    @doc(responses={200: s.TicketsSerializer}, operation_id="tickets_list", description="The caller's support tickets, newest activity first.")
    def get(self, request):
        return out(s.TicketsSerializer, {"items": [s.ticket_shape(row, with_messages=False) for row in tickets_for_user(request.user)[:100]]})

    @doc("ticket_invalid", "bad_request", "validation_failed", parameters=[IDEMPOTENCY_PARAMETER], request=s.TicketCreateSerializer, responses={201: s.TicketThreadSerializer}, operation_id="ticket_create", description="Open a ticket with its first message.")
    @idempotent
    def post(self, request):
        body = read_body(s.TicketCreateSerializer, request)
        try:
            ticket = create_ticket(request.user, body)
        except ValidationError as exc:
            raise ApiError("ticket_invalid", fields=domain_fields(exc)) from exc
        return out(s.TicketThreadSerializer, s.ticket_shape(ticket, with_messages=True), status=201)


class TicketView(MeBase):
    @doc("not_found", responses={200: s.TicketThreadSerializer}, operation_id="ticket_detail", description="One ticket with its thread.")
    def get(self, request, pk: int):
        return out(s.TicketThreadSerializer, s.ticket_shape(_my_ticket(pk, request.user), with_messages=True))


class TicketMessagesView(MeBase):
    @doc("not_found", "ticket_closed", "ticket_invalid", "bad_request", "validation_failed", parameters=[IDEMPOTENCY_PARAMETER], request=s.TicketReplySerializer, responses={201: s.TicketThreadSerializer}, operation_id="ticket_reply", description="Add a message to an open ticket.")
    @idempotent
    def post(self, request, pk: int):
        ticket = _my_ticket(pk, request.user)
        if ticket.status == TicketStatus.CLOSED:
            raise ApiError("ticket_closed")
        try:
            add_user_message(ticket, request.user, read_body(s.TicketReplySerializer, request))
        except ValidationError as exc:
            raise ApiError("ticket_invalid", fields=domain_fields(exc)) from exc
        return out(s.TicketThreadSerializer, s.ticket_shape(_my_ticket(pk, request.user), with_messages=True), status=201)


class TicketCloseView(MeBase):
    @doc("not_found", request=None, responses={200: s.TicketThreadSerializer}, operation_id="ticket_close", description="Close a ticket. Closing a closed ticket returns it as it is.")
    def post(self, request, pk: int):
        ticket = _my_ticket(pk, request.user)
        if ticket.status != TicketStatus.CLOSED:
            close_ticket_by_user(ticket, request.user)
        return out(s.TicketThreadSerializer, s.ticket_shape(_my_ticket(pk, request.user), with_messages=True))
