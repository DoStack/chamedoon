"""/api/v1/: the housing API of the new Mini App. Thin callers of home/; every error is `{code, detail}`.

Public views read no session and take no Authorization header, so the CDN can cache them.
"""

from __future__ import annotations

import logging
import os

from django.conf import settings
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from rest_framework.views import APIView

from api.authentication import TelegramJWTAuthentication
from api.permissions import IsTelegramUser
from home import conf, deeplink, freshness, gate
from home.feed import base_queryset, city_names, city_q, metro_channel
from home.models import HomeChannel, HomeCityWaitlist, HomeUserPrefs, ListingKind, PropertyType
from home.public import GONE, public_listing, visible_listing
from home_api.errors import ApiError, api_exception_handler
from home_api.schema import doc
from home_api.serializers import (
    AvailabilityRequestSerializer,
    AvailabilitySerializer,
    CitiesSerializer,
    ContactSerializer,
    DeepLinkSerializer,
    ExchangeRequestSerializer,
    ExchangeSerializer,
    FeedQuerySerializer,
    FeedSerializer,
    MetaSerializer,
    PrefsSerializer,
    PublicListingSerializer,
    ViewerListingSerializer,
    WaitlistRequestSerializer,
    WaitlistSerializer,
    clean_src,
)
from miniapp.auth import login_miniapp_user, startapp_path
from users.exceptions import TelegramAuthError
from users.services import is_blocked, upsert_telegram_user
from users.telegram import parse_and_validate_init_data
from users.tokens import issue_access_token

logger = logging.getLogger("home_api")

API_VERSION = 1
MIN_API_VERSION = 1
SNAPSHOT_SCHEMA_VERSION = 1
META_CACHE = "public, max-age=0, s-maxage=30"
DETAIL_CACHE = "public, max-age=0, s-maxage=120, stale-while-revalidate=600"
GONE_CACHE = "public, max-age=0, s-maxage=30"
DEEPLINK_CACHE = "public, max-age=0, s-maxage=60"
CITIES_CACHE = "public, max-age=0, s-maxage=300, stale-while-revalidate=3600"


class ExchangeThrottle(AnonRateThrottle):
    rate = "30/min"

    def get_ident(self, request) -> str:
        # Measured on a Preview: REMOTE_ADDR is the client, and Vercel overwrites a sent X-Forwarded-For. Off Vercel
        # that header is the caller's to write, so it never picks the bucket.
        return request.META.get("REMOTE_ADDR", "")


class WriteThrottle(UserRateThrottle):
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
        if self.obeys_maintenance and conf.row().maintenance_on:
            raise ApiError("maintenance")
        super().initial(request, *args, **kwargs)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        if "Cache-Control" not in response:
            response["Cache-Control"] = "no-store"
        return response


class MeView(V1View):
    authentication_classes = [TelegramJWTAuthentication]
    permission_classes = [IsTelegramUser]

    def initial(self, request, *args, **kwargs) -> None:
        super().initial(request, *args, **kwargs)
        if not conf.miniapp_v2_for(request.user.telegram_user_id):
            raise ApiError("miniapp_off")


def _cached(data: dict, cache_control: str) -> Response:
    return Response(data, headers={"Cache-Control": cache_control})


def _exchange_user(user) -> dict:
    return {
        "id": user.pk,
        "telegram_user_id": user.telegram_user_id,
        "first_name": user.first_name,
        "telegram_username": user.telegram_username,
    }


def _prefs_for(user) -> HomeUserPrefs:
    """The saved row, or unsaved defaults: a read never writes."""
    return HomeUserPrefs.objects.filter(user=user).first() or HomeUserPrefs(user=user)


class ExchangeView(V1View):
    throttle_classes = [ExchangeThrottle]

    @doc(
        "auth_outside",
        "auth_invalid",
        "session_expired",
        "user_blocked",
        "miniapp_off",
        "auth_unavailable",
        "bad_request",
        "validation_failed",
        "rate_limited",
        request=ExchangeRequestSerializer,
        responses={200: ExchangeSerializer},
        operation_id="auth_exchange",
        description=(
            "Trade initData for a token. Safe to retry. While old pages remain it also signs the session cookie, so links to them keep working. "
            "Flag off for this person: `miniapp_off`, with no token, no session and no user row. The old pages sign in through /app/login/."
        ),
    )
    def post(self, request):
        body = ExchangeRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        init_data = (body.validated_data.get("init_data") or "").strip()
        if not init_data:
            raise ApiError("auth_outside")
        try:
            identity = parse_and_validate_init_data(init_data)
        except TelegramAuthError as exc:
            raise ApiError(exc.code, exc.message) from exc
        if is_blocked(identity):
            raise ApiError("user_blocked")
        if not conf.miniapp_v2_for(identity.telegram_user_id):
            raise ApiError("miniapp_off")
        user = upsert_telegram_user(identity)
        src = clean_src(body.validated_data.get("src"))
        if src:
            prefs, _created = HomeUserPrefs.objects.get_or_create(user=user, defaults={"first_src": src})
            if not prefs.first_src:
                prefs.first_src = src
                prefs.save(update_fields=["first_src", "updated_at"])
        login_miniapp_user(request._request, user)
        return Response(
            {
                "access_token": issue_access_token(user),
                "expires_in": int(getattr(settings, "JWT_ACCESS_TOKEN_HOURS", 24 * 30)) * 3600,
                "user": _exchange_user(user),
                "prefs": PrefsSerializer(_prefs_for(user)).data,
                "miniapp_v2": conf.miniapp_v2_for(user.telegram_user_id),
            }
        )


class PrefsView(MeView):
    @doc(me=True, responses={200: PrefsSerializer}, operation_id="prefs_read")
    def get(self, request):
        return Response(PrefsSerializer(_prefs_for(request.user)).data)

    @doc(
        "validation_failed",
        "bad_request",
        "rate_limited",
        me=True,
        request=PrefsSerializer,
        responses={200: PrefsSerializer},
        operation_id="prefs_update",
        description="Saves only the fields sent. Sending the same values again changes nothing, so a retry is safe.",
    )
    def patch(self, request):
        self.throttle_classes = [WriteThrottle]
        self.check_throttles(request)
        prefs = _prefs_for(request.user)
        body = PrefsSerializer(prefs, data=request.data, partial=True)
        body.is_valid(raise_exception=True)
        body.save(user=request.user)
        return Response(body.data)


class WaitlistView(MeView):
    @doc(
        "validation_failed",
        "bad_request",
        "rate_limited",
        me=True,
        request=WaitlistRequestSerializer,
        responses={200: WaitlistSerializer, 201: WaitlistSerializer},
        operation_id="waitlist_join",
        description="Unique per person and city, so a retry is safe. A city that has a channel gives `city_listed` under `fields.city_name`.",
    )
    def post(self, request):
        self.throttle_classes = [WriteThrottle]
        self.check_throttles(request)
        body = WaitlistRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        name = body.validated_data["city_name"]
        _row, created = HomeCityWaitlist.objects.get_or_create(
            user=request.user,
            country=body.validated_data["country"],
            city_key=name.casefold(),
            defaults={"city_name": name},
        )
        return Response({"created": created, "city_name": name}, status=201 if created else 200)


class MeListingView(MeView):
    @doc(
        "listing_gone",
        me=True,
        responses={200: ViewerListingSerializer},
        operation_id="listing_viewer",
        description="The detail for a signed-in person, never cached: says if it is theirs (F11#9), and lets the owner open it while hidden.",
    )
    def get(self, request, pk: int):
        listing, state = visible_listing(pk)
        if listing is None:
            raise ApiError("listing_gone")
        is_owner = listing.post.author_id == request.user.telegram_user_id
        if state == GONE and not is_owner:
            raise ApiError("listing_gone")
        review_state = "live"
        if state == GONE:
            review_state = "in_review" if listing.needs_review else "hidden"
        data = public_listing(listing)
        data["state"] = "active" if state == GONE else data["state"]
        data["can_confirm"] = data["state"] == "active" and (is_owner or not freshness.is_app_listing(listing))
        return Response({"is_owner": is_owner, "review_state": review_state, "listing": data})


class ContactThrottle(UserRateThrottle):
    rate = "30/min"


class ContactView(MeView):
    throttle_classes = [ContactThrottle]

    @doc(
        "listing_gone",
        "listing_closed",
        "no_channel",
        "not_member",
        "gate_banned",
        "reveal_cap",
        "rate_limited",
        me=True,
        request=None,
        responses={200: ContactSerializer},
        operation_id="listing_contact",
        description=(
            "«تماس» and «دیدن پست اصلی» both call this. Safe to retry: a listing already opened in the window never counts again, "
            "so no idempotency key. Also call it again when the app regains focus after `not_member`. "
            "A Telegram error at the gate fails open and answers `membership_checked: false`."
        ),
    )
    def post(self, request, pk: int):
        listing, state = visible_listing(pk)
        if listing is None or state == GONE:
            raise ApiError("listing_gone")
        try:
            found = gate.reveal(request.user, listing)
        except gate.GateRefused as exc:
            extra = {}
            if exc.code == "not_member":
                extra = {"join_url": gate.join_url(exc.channel), "channel": _channel_row(exc.channel)}
            raise ApiError(exc.code, **extra) from exc
        return Response(
            {
                "telegram": found.telegram,
                "telegram_url": found.telegram_url,
                "phone": found.phone,
                "source_url": found.source_url,
                "has_any": found.has_any,
                "membership_checked": found.membership_checked,
                "gate": found.gate,
                "reveals_left": found.reveals_left,
            }
        )


class AvailabilityView(MeView):
    @doc(
        "listing_gone",
        "confirm_not_allowed",
        "validation_failed",
        "bad_request",
        "rate_limited",
        me=True,
        request=AvailabilityRequestSerializer,
        responses={200: AvailabilitySerializer},
        operation_id="listing_availability",
        description="F10: is it still available. One answer per person and listing, so a retry is safe and no idempotency key is needed.",
    )
    def post(self, request, pk: int):
        self.throttle_classes = [WriteThrottle]
        self.check_throttles(request)
        body = AvailabilityRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        listing, state = visible_listing(pk)
        if listing is None or state == GONE:
            raise ApiError("listing_gone")
        telegram_id = request.user.telegram_user_id
        if freshness.is_app_listing(listing) and not freshness.is_owner(listing, telegram_id):
            raise ApiError("confirm_not_allowed")
        if body.validated_data["answer"] == "taken":
            outcome = freshness.mark_taken(listing, telegram_id, request.user)
        else:
            outcome = "confirmed" if freshness.confirm_available(listing, telegram_id, request.user) else "closed"
        return Response({"outcome": outcome})


class MetaView(V1View):
    obeys_maintenance = False

    @doc(maintenance=False, responses={200: MetaSerializer}, operation_id="meta", description="Answers during maintenance too. Cached for 30 seconds.")
    def get(self, request):
        row = conf.row()
        return _cached(
            {
                "api_version": API_VERSION,
                "min_api_version": MIN_API_VERSION,
                "build_id": (os.environ.get("VERCEL_GIT_COMMIT_SHA") or "dev")[:12],
                "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
                "miniapp_v2": row.miniapp_v2,
                "contact_gate_on": row.contact_gate_on,
                "snapshots_on": row.snapshots_on,
                "alerts_on": row.alerts_enabled,
                "maintenance": {"on": row.maintenance_on, "until": row.maintenance_until},
            },
            META_CACHE,
        )


def _channel_row(channel: HomeChannel | None) -> dict | None:
    if channel is None:
        return None
    return {"username": channel.username, "title": channel.title, "city": channel.city}


class CitiesView(V1View):
    @doc(responses={200: CitiesSerializer}, operation_id="cities", description="The launch cities: enabled channels only. Cached for 5 minutes.")
    def get(self, request):
        names = city_names()
        rows: dict[str, dict] = {}
        for channel in HomeChannel.objects.filter(enabled=True).order_by("country", "city"):
            country = rows.setdefault(channel.country, {"code": channel.country, "channel": None, "cities": []})
            if not channel.city:
                country["channel"] = _channel_row(channel)
                continue
            filters = {"country": channel.country, "kind": ListingKind.OFFER, "city": channel.city}
            country["cities"].append(
                {
                    "slug": deeplink.slug(channel.city),
                    "name": channel.city,
                    "name_fa": names.get(channel.city.casefold(), channel.city),
                    "listing_count": base_queryset(filters).filter(city_q(filters)).count(),
                    "channel": _channel_row(channel),
                }
            )
        return _cached({"countries": list(rows.values())}, CITIES_CACHE)


class PublicListingView(V1View):
    @doc(
        "listing_gone",
        responses={200: PublicListingSerializer},
        operation_id="listing_detail",
        description="Cached: no session, no cookie, and do not send Authorization. Can be 2 minutes old, so the contact call makes the final check.",
    )
    def get(self, request, pk: int):
        listing, state = visible_listing(pk)
        if listing is None or state == GONE:
            error = ApiError("listing_gone")
            response = api_exception_handler(error, {"request": request})
            response["Cache-Control"] = GONE_CACHE
            return response
        return _cached(public_listing(listing), DETAIL_CACHE)


def _salvage(value: str) -> str:
    """A bad `src-` tag must not lose the listing it came with."""
    tokens = value.split("_")
    return "_".join(token for token in tokens if not token.startswith("src-") or deeplink._SOURCE.match(token))


def resolve_start(value: str) -> dict:
    result = {
        "kind": "landing",
        "malformed": False,
        "country": "",
        "city": "",
        "city_slug": "",
        "city_known": False,
        "listing_id": None,
        "listing_state": None,
        "filters": {"types": [], "min": None, "max": None},
        "src": "",
        "legacy_path": "",
    }
    value = (value or "").strip()
    if not value or value == "home":
        return result

    def from_listing(pk: int) -> None:
        listing, state = visible_listing(pk)
        result["kind"] = "listing"
        result["listing_state"] = state
        result["listing_id"] = listing.pk if listing is not None and state != GONE else pk
        if listing is not None and state != GONE:
            data = public_listing(listing)
            result["country"] = data["country"]
            result["city"] = data["metro"]
            result["city_slug"] = data["metro_slug"]
            result["city_known"] = bool(data["metro"])

    lowered = value.lower()
    if lowered.startswith("home_") and lowered.removeprefix("home_").isdigit():
        from_listing(int(lowered.removeprefix("home_")))
        return result

    start = deeplink.parse(lowered if len(lowered) <= deeplink.START_LIMIT else "")
    if start is None:
        start = deeplink.parse(_salvage(lowered)) if len(lowered) <= deeplink.START_LIMIT else None
    if start is None:
        path = startapp_path(value)
        if path != "/app/":
            result.update(kind="legacy", legacy_path=path)
            return result
        logger.info("deeplink_bad value=%r", value[:64])
        result["malformed"] = True
        return result

    result["src"] = start.source
    result["filters"] = {
        "types": [start.type] if start.type in PropertyType.values else [],
        "min": start.min_price,
        "max": start.max_price,
    }
    result["country"] = start.country
    if not HomeChannel.objects.filter(country=start.country, enabled=True).exists():
        logger.info("deeplink_bad value=%r", value[:64])
        result.update(country="", malformed=True)
        return result
    result["kind"] = "country"
    if start.city:
        channel = metro_channel(start.country, start.city)
        result["kind"] = "city"
        result["city_slug"] = start.city
        if channel is not None and channel.enabled:
            result.update(city=channel.city, city_slug=deeplink.slug(channel.city), city_known=True)
    if start.listing:
        from_listing(start.listing)
        if result["listing_state"] == GONE:
            result["country"] = start.country
    return result


class DeepLinkView(V1View):
    @doc(
        responses={200: DeepLinkSerializer},
        parameters=[OpenApiParameter("startapp", str, description="Telegram's start_param. Missing or empty: a normal open.")],
        operation_id="deeplink_resolve",
        description="Where a link goes. A broken link is never an error: it answers `malformed: true`, a normal open. A gone or closed listing answers its state.",
    )
    def get(self, request):
        return _cached(resolve_start(request.query_params.get("startapp", "")), DEEPLINK_CACHE)


class FeedView(V1View):
    @doc(
        "validation_failed",
        "feed_failed",
        "rate_limited",
        served="PR 4: answers not_served_yet until then",
        parameters=[FeedQuerySerializer],
        responses={200: FeedSerializer},
        operation_id="feed_fallback",
        description="Only when the manifest or a snapshot fails. Same card shape as a snapshot.",
    )
    def get(self, request):
        raise ApiError("not_served_yet")


class NotFoundView(V1View):
    """Any other path under /api/v1/ still answers with a code, never Django's HTML 404."""

    obeys_maintenance = False

    def initial(self, request, *args, **kwargs) -> None:
        raise ApiError("not_found")

    @extend_schema(exclude=True)
    def get(self, request, *args, **kwargs):
        raise ApiError("not_found")

