"""/api/v1/: the housing API of the new Mini App. Thin callers of home/; every error is `{code, detail}`.

Public views read no session and take no Authorization header, so the CDN can cache them.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from datetime import date, timedelta

from django.conf import settings
from django.http import Http404, HttpResponse
from django.db.models import Prefetch, Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import ErrorDetail, ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from rest_framework.views import APIView

from api.authentication import TelegramJWTAuthentication
from api.permissions import IsTelegramUser
from home import alert_store, conf, deeplink, freshness, gate, inbox, owner_posts, share, smart_post, uploads
from home import bot as home_bot
from home.alerts import MAX_ALERTS_PER_USER
from home.alerts import active_count as alert_active_count
from home.areas import city_areas, search_areas
from home.card_filters import matches, sort_cards
from home.feed import base_queryset, city_names, city_q, countries, metro_channel
from home.models import (
    HomeSearchQuery,
    HomeAlert,
    HomeChannel,
    HomeCityWaitlist,
    HomeMedia,
    HomeNotification,
    HomeReport,
    HomeSaved,
    HomeUserPrefs,
    ListingKind,
    ListingStatus,
    MediaStatus,
    PropertyType,
    ReportReason,
)
from home.public import GONE, channel_title, closed_reason, follow_merged, listing_state, public_card, public_listing, visible_listing
from home.publish import city_channels
from home.storage import UPLOAD_URL_SECONDS, get_storage
from home.snapshots import SCHEMA_VERSION, feed_cards, manifest_url
from home.models import StatKind
from home.stats import owner_stats, record as record_stat
from home.submit import user_listings
from home_api.errors import ApiError, api_exception_handler
from telemetry import capture, capture_user
from telemetry.events import is_new_user
from home_api.schema import doc
from home_api.serializers import (
    JoinCheckRequestSerializer,
    JoinCheckSerializer,
    OutreachAlertSerializer,
    OutreachLandingRequestSerializer,
    OutreachLandingSerializer,
    PostParseRequestSerializer,
    PostParseSerializer,
    ListingEditRequestSerializer,
    ListingStatusRequestSerializer,
    MineFormSerializer,
    MineListSerializer,
    MineRowSerializer,
    PhotoUploadRequestSerializer,
    PhotoUploadTicketSerializer,
    PostCreateRequestSerializer,
    PostResultSerializer,
    UploadedPhotoSerializer,
    AccountSerializer,
    AlertCreateRequestSerializer,
    AlertListingsSerializer,
    AlertListSerializer,
    AlertRowSerializer,
    AlertSaveSerializer,
    AlertUpdateRequestSerializer,
    NotificationListSerializer,
    NotificationReadRequestSerializer,
    NotificationReadSerializer,
    AreaSearchQuerySerializer,
    AreaSearchSerializer,
    CityAreasSerializer,
    CityQuerySerializer,
    SavedIdsSerializer,
    SavedListSerializer,
    SavedQuerySerializer,
    SavedRequestSerializer,
    SavedRowSerializer,
    AvailabilityRequestSerializer,
    AvailabilitySerializer,
    CitiesSerializer,
    ContactSerializer,
    ShareSerializer,
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
from miniapp.home_views import alert_filters, new_counts
from users.exceptions import TelegramAuthError
from users.services import is_blocked, upsert_telegram_user
from users.telegram import parse_and_validate_init_data
from users.tokens import issue_access_token

logger = logging.getLogger("home_api")

API_VERSION = 1
MIN_API_VERSION = 1
META_CACHE = "public, max-age=0, s-maxage=30"
DETAIL_CACHE = "public, max-age=0, s-maxage=120, stale-while-revalidate=600"
GONE_CACHE = "public, max-age=0, s-maxage=30"
DEEPLINK_CACHE = "public, max-age=0, s-maxage=60"
CITIES_CACHE = "public, max-age=0, s-maxage=300, stale-while-revalidate=3600"
FEED_CACHE = "public, max-age=0, s-maxage=60"
FEED_PAGE = 60
SAVED_PAGE = 50
NOTIFICATION_PAGE = 30
IDEMPOTENCY_KEY = OpenApiParameter(
    "Idempotency-Key", str, OpenApiParameter.HEADER, required=True, description="One per draft. The same key again answers the first result and posts nothing new."
)


# Each has its own scope: DRF keys its counters by scope and client, so sharing one would share one budget.
class ExchangeThrottle(AnonRateThrottle):
    scope = "home_exchange"
    rate = "30/min"

    def get_ident(self, request) -> str:
        # Measured on a Preview: REMOTE_ADDR is the client, and Vercel overwrites a sent X-Forwarded-For. Off Vercel
        # that header is the caller's to write, so it never picks the bucket.
        return request.META.get("REMOTE_ADDR", "")


class WriteThrottle(UserRateThrottle):
    scope = "home_write"
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


def _store_first_src(user, src: str) -> None:
    from outreach.housing import note_open

    prefs, _created = HomeUserPrefs.objects.get_or_create(user=user, defaults={"first_src": src})
    if not prefs.first_src:
        prefs.first_src = src
        prefs.save(update_fields=["first_src", "updated_at"])
    note_open(src, user)


def _auth_failed(code: str) -> None:
    capture("miniapp_auth", source="api", side="housing", properties={"result": "failure", "code": code})


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
            "Flag off for this person: `miniapp_off`, with no token and no session; only a link's `src` is stored. The old pages sign in through /app/login/."
        ),
    )
    def post(self, request):
        body = ExchangeRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        init_data = (body.validated_data.get("init_data") or "").strip()
        if not init_data:
            _auth_failed("auth_outside")
            raise ApiError("auth_outside")
        try:
            identity = parse_and_validate_init_data(init_data)
        except TelegramAuthError as exc:
            _auth_failed(exc.code)
            raise ApiError(exc.code, exc.message) from exc
        if is_blocked(identity):
            _auth_failed("user_blocked")
            raise ApiError("user_blocked")
        src = clean_src(body.validated_data.get("src"))
        if not conf.miniapp_v2_for(identity.telegram_user_id):
            # Attribution does not wait for the flag, so a link tag is the one thing kept.
            if src:
                _store_first_src(upsert_telegram_user(identity), src)
            _auth_failed("miniapp_off")
            raise ApiError("miniapp_off")
        user = upsert_telegram_user(identity)
        if src:
            _store_first_src(user, src)
        login_miniapp_user(request._request, user)
        capture_user("miniapp_auth", user, source="api", side="housing", properties={"result": "success", "code": "", "new_user": is_new_user(user), "src": src or ""})
        qa = conf.qa_overrides(user.telegram_user_id)
        return Response(
            {
                "access_token": issue_access_token(user),
                "expires_in": int(getattr(settings, "JWT_ACCESS_TOKEN_HOURS", 24 * 30)) * 3600,
                "user": _exchange_user(user),
                "prefs": PrefsSerializer(_prefs_for(user)).data,
                "miniapp_v2": conf.miniapp_v2_for(user.telegram_user_id),
                **({"qa": qa} if qa else {}),
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


def _posting_open(request) -> None:
    if not conf.feature_for("posting_api_on", request.user.telegram_user_id):
        raise ApiError("feature_off")


def _post_form(validated: dict) -> dict:
    """Plain JSON values, since the form is stored as review data."""
    form = {key: value.isoformat() if isinstance(value, date) and key != "base_updated_at" else value for key, value in validated.items()}
    return owner_posts.with_area_name(form)


@contextmanager
def _post_errors():
    try:
        yield
    except owner_posts.PostRefused as exc:
        raise ApiError(exc.code, **exc.extra) from exc
    except owner_posts.FieldProblem as exc:
        raise ValidationError({exc.field: ErrorDetail("Fix this field.", code=exc.code)}) from exc
    except uploads.UploadError as exc:
        raise ApiError(exc.code) from exc


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
        is_owner = freshness.is_owner(listing, request.user.telegram_user_id)
        if state == GONE and (not is_owner or listing.status == ListingStatus.REMOVED):
            raise ApiError("listing_gone")
        extra = {}
        if is_owner:
            from outreach.housing import owner_opened

            owner_opened(listing, request.user)
            extra["stats"] = owner_stats(listing)
        else:
            record_stat(listing, request.user.telegram_user_id, StatKind.VIEW)
        review_state = "live"
        if state == GONE:
            review_state = "in_review" if listing.needs_review else "hidden"
        data = public_listing(listing)
        data["state"] = "active" if state == GONE else data["state"]
        data["can_confirm"] = data["state"] == "active" and (is_owner or not freshness.is_app_listing(listing))
        return Response({"is_owner": is_owner, "review_state": review_state, "listing": data, **extra})

    @doc(
        "feature_off",
        "listing_gone",
        "listing_closed",
        "edit_conflict",
        "duplicate_listing",
        "photo_gone",
        "validation_failed",
        "bad_request",
        "rate_limited",
        me=True,
        request=ListingEditRequestSerializer,
        responses={200: PostResultSerializer},
        operation_id="listing_edit",
        description="F20#2. The whole form again, through the same checks as a post. It goes back through review; the old version stays up until that passes. A retry is safe: the second call sees the same values.",
    )
    def patch(self, request, pk: int):
        _posting_open(request)
        self.throttle_classes = [WriteThrottle]
        self.check_throttles(request)
        body = ListingEditRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        form = _post_form(body.validated_data)
        with _post_errors():
            listing = owner_posts.own_listing(request.user, pk)
            return Response(owner_posts.edit_listing(request.user, listing, form, locale=_prefs_for(request.user).locale, countries=countries()))

    @doc(
        "feature_off",
        "rate_limited",
        me=True,
        request=None,
        responses={204: None},
        operation_id="listing_delete",
        description="F20#6. The channel post changes to closed, and the old link answers `listing_gone`. Removing what is not there is a success, so a retry is safe.",
    )
    def delete(self, request, pk: int):
        _posting_open(request)
        self.throttle_classes = [WriteThrottle]
        self.check_throttles(request)
        owner_posts.delete_listing(request.user, pk)
        return Response(status=204)


class ContactThrottle(UserRateThrottle):
    scope = "home_contact"
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
                chats = gate.chat_rows(exc.channel, exc.missing)
                extra = {"join_url": chats[0]["join_url"] if chats else gate.join_url(exc.channel), "channel": _channel_row(exc.channel), "chats": chats}
            raise ApiError(exc.code, **extra) from exc
        record_stat(listing, request.user.telegram_user_id, StatKind.CONTACT)
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


class ShareThrottle(ExchangeThrottle):
    scope = "home_share"
    rate = "30/min"


class ShareView(MeView):
    throttle_classes = [ShareThrottle]

    @doc(
        "listing_gone",
        "rate_limited",
        me=True,
        request=None,
        responses={200: ShareSerializer},
        operation_id="listing_share",
        description=(
            "«اشتراک»: the ad as a group post with a link back, never a contact. `prepared_id` is for `WebApp.shareMessage` "
            "(Bot API 8.0); blank when Telegram refused, then send `text` through t.me/share/url."
        ),
    )
    def post(self, request, pk: int):
        listing, state = visible_listing(pk)
        if listing is None or state == GONE:
            raise ApiError("listing_gone")
        return Response(share.share_message(listing, request.user.telegram_user_id))


class JoinCheckView(MeView):
    throttle_classes = [ContactThrottle]

    @doc(
        "validation_failed",
        "rate_limited",
        me=True,
        request=JoinCheckRequestSerializer,
        responses={200: JoinCheckSerializer},
        operation_id="join_check",
        description=(
            "Before «ثبت آگهی»: the city's channel and group this person still has to join. Read-only, so safe to retry. "
            "Call again after the join and when the app regains focus. Off, no channel, or a Telegram error: `chats` is empty."
        ),
    )
    def post(self, request):
        body = JoinCheckRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return Response(gate.join_check(request.user, country=body.validated_data["country"], city=body.validated_data["city"]))


PHOTO_CACHE = "public, max-age=86400, s-maxage=604800"


def chat_photo(request, pk: int, kind: str):
    """A gate chat's avatar through the bot, so the token stays on the server. Cached a week at the edge."""
    channel = HomeChannel.objects.filter(pk=pk, enabled=True).first()
    if channel is None or kind not in {"channel", "group"} or (kind == "group" and not channel.has_group()):
        raise Http404
    data = home_bot.chat_photo(channel.target() if kind == "channel" else channel.group_target())
    if not data:
        response = HttpResponse(status=404)
        response["Cache-Control"] = "public, max-age=3600"
        return response
    response = HttpResponse(data, content_type="image/jpeg")
    response["Cache-Control"] = PHOTO_CACHE
    return response


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
                "snapshot_schema_version": SCHEMA_VERSION,
                "manifest_url": manifest_url(),
                "miniapp_v2": row.miniapp_v2,
                "contact_gate_on": row.contact_gate_on,
                "snapshots_on": row.snapshots_on,
                "alerts_on": row.alerts_enabled,
                "text_search_on": row.ai_search_enabled,
                "map_style_url": settings.HOME_MAP_STYLE_URL,
                "maintenance": {"on": row.maintenance_on, "until": row.maintenance_until},
                "features": {"alerts": row.alerts_api_on, "account": row.account_api_on, "posting": row.posting_api_on, "smart_posting": row.smart_posting_on},
            },
            META_CACHE,
        )


def _channel_row(channel: HomeChannel | None) -> dict | None:
    if channel is None:
        return None
    return {"username": channel.username, "title": channel_title(channel), "city": channel.city}


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
        "path": "",
    }
    value = (value or "").strip()
    if not value or value == "home":
        return result
    if value.lower() in deeplink.PAGES:
        result.update(kind="page", path=deeplink.PAGES[value.lower()])
        return result
    if found := deeplink.SEARCH.match(value.lower()):
        return _search_start(result, int(found.group(1)), found.group(2) or "")

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
            result["path"] = deeplink.list_path(result["city_slug"], result["filters"])
    if start.listing:
        from_listing(start.listing)
        if result["listing_state"] == GONE:
            result["country"] = start.country
    return result


def _search_start(result: dict, pk: int, src: str = "") -> dict:
    """`home_q<id>[_src-<tag>]`: a search the housing bot or an outreach chat made, opened with all of its filters.
    The tag tracks who opened it, as on every other link."""
    query = HomeSearchQuery.objects.filter(pk=pk, parser__in=["assistant", "outreach"]).first()
    result["src"] = src
    channel = metro_channel(query.country, query.city) if query is not None else None
    if channel is None or not channel.enabled:
        result["malformed"] = True
        return result
    parsed = query.parsed or {}
    result.update(kind="city", country=channel.country, city=channel.city, city_slug=deeplink.slug(channel.city), city_known=True)
    result["filters"] = {"types": list(parsed.get("types") or []), "min": parsed.get("min"), "max": parsed.get("max")}
    result["path"] = deeplink.list_path(result["city_slug"], parsed)
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


class FeedThrottle(ExchangeThrottle):
    scope = "home_feed"
    rate = "60/min"


class FeedView(V1View):
    throttle_classes = [FeedThrottle]

    @doc(
        "validation_failed",
        "feed_failed",
        "rate_limited",
        parameters=[FeedQuerySerializer],
        responses={200: FeedSerializer},
        operation_id="feed_fallback",
        description=(
            "Only when the manifest or a snapshot fails. Same card shape and the same filter and sort rules as the client "
            "runs over a snapshot. Cached for a minute, so do not send Authorization."
        ),
    )
    def get(self, request):
        query = FeedQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        filters = dict(query.validated_data)
        country = filters["country"].upper()
        try:
            channel = metro_channel(country, filters["city"])
            if channel is None or not channel.enabled:
                raise ValidationError({"city": ErrorDetail("No such city.", code="invalid_choice")})
            cards = [card for card in feed_cards(channel) if matches(card, filters)]
            ordered = sort_cards(cards, filters.get("sort") or "new", move=filters.get("move"))
        except ValidationError:
            raise
        except Exception as exc:
            logger.exception("feed_failed country=%s", country)
            raise ApiError("feed_failed") from exc
        start = int(filters.get("cursor") or 0)
        end = start + FEED_PAGE
        return _cached(
            {
                "generated_at": timezone.now(),
                "results": ordered[start:end],
                "total": len(ordered),
                "next_cursor": str(end) if end < len(ordered) else None,
            },
            FEED_CACHE,
        )


class AreasThrottle(ExchangeThrottle):
    scope = "home_areas"
    rate = "60/min"


def _city_channel(query) -> HomeChannel:
    channel = metro_channel(query["country"].upper(), query["city"])
    if channel is None or not channel.enabled:
        raise ValidationError({"city": ErrorDetail("No such city.", code="invalid_choice")})
    return channel


class CityAreasView(V1View):
    throttle_classes = [AreasThrottle]

    @doc(
        "validation_failed",
        "rate_limited",
        parameters=[CityQuerySerializer],
        responses={200: CityAreasSerializer},
        operation_id="city_areas",
        description="F07#1, #2 and the map's points: a metro's areas, by this week's count. Cached for 5 minutes, so the order holds through a session.",
    )
    def get(self, request):
        query = CityQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        channel = _city_channel(query.validated_data)
        return _cached(city_areas(channel, feed_cards(channel)), CITIES_CACHE)


class AreaSearchView(V1View):
    throttle_classes = [AreasThrottle]

    @doc(
        "validation_failed",
        "rate_limited",
        parameters=[AreaSearchQuerySerializer],
        responses={200: AreaSearchSerializer},
        operation_id="area_search",
        description="F07#3 to #5: names and other names only, in every city. Empty lists: F07#5. Counts for `here` come from public/areas/.",
    )
    def get(self, request):
        query = AreaSearchQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data
        here_country, here_city = (data.get("country") or "").upper(), data.get("city") or ""
        here = metro_channel(here_country, here_city) if here_country and here_city else None
        rows = {
            side: [
                {
                    "id": area.pk,
                    "name_fa": area.fa,
                    "matched": matched,
                    "country": area.country,
                    "city": area.city,
                    "city_slug": deeplink.slug(area.city),
                }
                for area, matched in hits
            ]
            for side, hits in search_areas(data["q"], here).items()
        }
        return _cached(rows, DEEPLINK_CACHE)


def _saved_rows(user) -> list[HomeSaved]:
    """A person's rows, one per listing they now show. A merge can leave two; the newest stays, keeping a note if it has none."""
    media = HomeMedia.objects.filter(status=MediaStatus.STORED).order_by("position")
    rows = (
        HomeSaved.objects.filter(user=user)
        .select_related("listing__post", "listing__home_area", "listing__canonical__post", "listing__canonical__home_area")
        .prefetch_related(
            Prefetch("listing__post__media", queryset=media, to_attr="stored_media"),
            Prefetch("listing__canonical__post__media", queryset=media, to_attr="stored_media"),
        )
    )
    kept: dict[object, HomeSaved] = {}
    for saved in rows:
        target = follow_merged(saved.listing)
        key = target.pk if target is not None else f"gone-{saved.pk}"
        if key in kept:
            kept[key].note = kept[key].note or saved.note
        else:
            kept[key] = saved
    return list(kept.values())


def _saved_payload(saved: HomeSaved, channels, storage, taken: set[int]) -> dict:
    listing = follow_merged(saved.listing)
    state = listing_state(listing) if listing is not None else GONE
    shown = listing is not None and state != GONE
    return {
        "listing_id": saved.listing_id,
        "state": state,
        "card": public_card(listing, channels, storage=storage) if shown else None,
        "closed_reason": closed_reason(listing, taken=listing.pk in taken) if shown and state == "closed" else None,
        "note": saved.note,
        "saved_at": saved.created_at,
    }


def _saved_page(rows: list[HomeSaved]) -> list[dict]:
    """Looks the channels, storage and reports up once for the whole page."""
    channels = city_channels()
    storage = get_storage()
    targets = [listing.pk for listing in (follow_merged(saved.listing) for saved in rows) if listing is not None]
    taken = set(HomeReport.objects.filter(listing_id__in=targets, reason=ReportReason.TAKEN).values_list("listing_id", flat=True))
    return [_saved_payload(saved, channels, storage, taken) for saved in rows]


class SavedListView(MeView):
    @doc(
        "validation_failed",
        me=True,
        parameters=[SavedQuerySerializer],
        responses={200: SavedListSerializer},
        operation_id="saved_list",
        description="F14: newest first, 50 a page. Closed items stay (F14#4), gone ones come with `card: null` (F14#8), merged ones show the listing they became, once.",
    )
    def get(self, request):
        query = SavedQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        start = int(query.validated_data.get("cursor") or 0)
        rows = _saved_rows(request.user)
        end = start + SAVED_PAGE
        return Response(
            {
                "results": _saved_page(rows[start:end]),
                "total": len(rows),
                "next_cursor": str(end) if end < len(rows) else None,
            }
        )


class SavedIdsView(MeView):
    @doc(me=True, responses={200: SavedIdsSerializer}, operation_id="saved_ids", description="Every saved listing id, to fill the bookmark on cards.")
    def get(self, request):
        pairs = HomeSaved.objects.filter(user=request.user).values_list("listing_id", "listing__canonical_id")
        return Response({"listing_ids": sorted({pk for pair in pairs for pk in pair if pk})})


class SavedItemView(MeView):
    @doc(
        "listing_gone",
        "validation_failed",
        "bad_request",
        "rate_limited",
        me=True,
        request=SavedRequestSerializer,
        responses={200: SavedRowSerializer},
        operation_id="saved_put",
        description="Mark a listing, or change its note. Undo of a removal is this same call. One row per person and listing, so a retry is safe. A merged listing saves the one it became.",
    )
    def put(self, request, pk: int):
        self.throttle_classes = [WriteThrottle]
        self.check_throttles(request)
        body = SavedRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        listing, state = visible_listing(pk)
        if listing is None or state == GONE:
            raise ApiError("listing_gone")
        # A row on a listing merged into this one already means this one, so marking it again adds no second row.
        saved = HomeSaved.objects.filter(user=request.user).filter(Q(listing=listing) | Q(listing__canonical=listing)).first()
        if saved is None:
            saved, _created = HomeSaved.objects.get_or_create(user=request.user, listing=listing)
            record_stat(listing, request.user.telegram_user_id, StatKind.SAVE)
        if "note" in body.validated_data:
            saved.note = body.validated_data["note"].strip()
            saved.save(update_fields=["note", "updated_at"])
        return Response(_saved_page([saved])[0])

    @doc(
        "rate_limited",
        me=True,
        request=None,
        responses={204: None},
        operation_id="saved_delete",
        description="Remove a mark, gone listings too. Removing what is not there is a success, so a retry is safe. The note goes with it, so an undo sends it back.",
    )
    def delete(self, request, pk: int):
        self.throttle_classes = [WriteThrottle]
        self.check_throttles(request)
        # Every row showing this listing goes, or a merged twin would bring it back.
        target, _state = visible_listing(pk)
        match = Q(listing_id=pk) | Q(listing__canonical_id=pk)
        if target is not None:
            match |= Q(listing=target) | Q(listing__canonical=target)
        HomeSaved.objects.filter(user=request.user).filter(match).delete()
        return Response(status=204)


class FeatureView(MeView):
    """A /me screen with its own switch in Home settings."""

    feature = ""

    def initial(self, request, *args, **kwargs) -> None:
        super().initial(request, *args, **kwargs)
        if not conf.feature_for(self.feature, request.user.telegram_user_id):
            raise ApiError("feature_off")


def _bot_url() -> str:
    return f"https://t.me/{conf.bot_username()}"


def _alert_payload(alert: HomeAlert, new_count: int = 0) -> dict:
    return alert_store.alert_row(alert, alert_filters(alert), new_count)


def _own_alert(request, pk: int) -> HomeAlert:
    alert = HomeAlert.objects.filter(pk=pk, telegram_user_id=request.user.telegram_user_id).first()
    if alert is None:
        raise ApiError("alert_gone")
    return alert


class AccountView(FeatureView):
    feature = "account_api_on"

    @doc("feature_off", me=True, responses={200: AccountSerializer}, operation_id="account_read", description="F21 and the two badges.")
    def get(self, request):
        user, telegram_id = request.user, request.user.telegram_user_id
        prefs = _prefs_for(user)
        channel = metro_channel(prefs.country, prefs.city) if prefs.city else None
        alerts = list(HomeAlert.objects.filter(telegram_user_id=telegram_id))
        return Response(
            {
                "user": _exchange_user(user),
                "prefs": PrefsSerializer(prefs).data,
                "counts": {
                    "saved": HomeSaved.objects.filter(user=user).count(),
                    "listings": user_listings(user).exclude(status=ListingStatus.REMOVED).count(),
                    "alerts_active": alert_active_count(telegram_id),
                    "alerts_new": sum(new_counts(alerts).values()),
                    "unread": inbox.unread(user),
                },
                "channel": _channel_row(channel) if channel and channel.enabled else None,
                "bot_open": alert_store.bot_open(telegram_id),
                "bot_blocked": alert_store.bot_blocked(telegram_id),
                "bot_url": _bot_url(),
            }
        )


class AlertsView(FeatureView):
    feature = "alerts_api_on"

    @doc("feature_off", me=True, responses={200: AlertListSerializer}, operation_id="alerts_list", description="F16: newest first, with no paging, since the cap keeps it short.")
    def get(self, request):
        telegram_id = request.user.telegram_user_id
        alerts = list(HomeAlert.objects.filter(telegram_user_id=telegram_id).order_by("-created_at", "-id"))
        counts = new_counts(alerts)
        return Response(
            {
                "results": [_alert_payload(alert, counts.get(alert.pk, 0)) for alert in alerts],
                "active_count": alert_active_count(telegram_id),
                "cap": MAX_ALERTS_PER_USER,
                "bot_open": alert_store.bot_open(telegram_id),
                "bot_blocked": alert_store.bot_blocked(telegram_id),
                "bot_url": _bot_url(),
                "notify_enabled": _prefs_for(request.user).notify_enabled,
            }
        )

    @doc(
        "feature_off",
        "alert_cap",
        "validation_failed",
        "bad_request",
        "rate_limited",
        me=True,
        request=AlertCreateRequestSerializer,
        responses={200: AlertSaveSerializer, 201: AlertSaveSerializer},
        operation_id="alert_create",
        description="F15. The same search finds the same row, so a retry is safe and no idempotency key is needed. Saved with zero matches too (F15#6). `city` under `fields`: no such city.",
    )
    def post(self, request):
        self.throttle_classes = [WriteThrottle]
        self.check_throttles(request)
        body = AlertCreateRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        channel = _city_channel(data)
        try:
            alert, outcome = alert_store.save_alert(request.user, channel.country, channel.city, data.get("filters") or {}, data["delivery"])
        except alert_store.AlertCapReached as exc:
            raise ApiError("alert_cap") from exc
        return Response(
            {"save_outcome": outcome, "alert": _alert_payload(alert), "bot_open": alert_store.bot_open(request.user.telegram_user_id), "bot_url": _bot_url()},
            status=201 if outcome == "created" else 200,
        )


class AlertView(FeatureView):
    feature = "alerts_api_on"

    @doc("feature_off", "alert_gone", me=True, responses={200: AlertRowSerializer}, operation_id="alert_read", description="F16#12 when it is gone.")
    def get(self, request, pk: int):
        alert = _own_alert(request, pk)
        return Response(_alert_payload(alert, new_counts([alert]).get(alert.pk, 0)))

    @doc(
        "feature_off",
        "alert_gone",
        "alert_cap",
        "alert_duplicate",
        "alert_not_editable",
        "validation_failed",
        "bad_request",
        "rate_limited",
        me=True,
        request=AlertUpdateRequestSerializer,
        responses={200: AlertRowSerializer},
        operation_id="alert_update",
        description="Saves only the fields sent, so a retry is safe. `alert_state` works on a wanted alert; `filters` and `delivery` answer `alert_not_editable`. Turning on at the cap: `alert_cap`.",
    )
    def patch(self, request, pk: int):
        self.throttle_classes = [WriteThrottle]
        self.check_throttles(request)
        body = AlertUpdateRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        alert = _own_alert(request, pk)
        try:
            alert = alert_store.update_alert(request.user, alert, body.validated_data)
        except alert_store.AlertCapReached as exc:
            raise ApiError("alert_cap") from exc
        except alert_store.AlertNotEditable as exc:
            raise ApiError("alert_not_editable") from exc
        except alert_store.AlertDuplicate as exc:
            raise ApiError("alert_duplicate", duplicate_id=exc.duplicate_id) from exc
        return Response(_alert_payload(alert, new_counts([alert]).get(alert.pk, 0)))

    @doc("feature_off", "rate_limited", me=True, request=None, responses={204: None}, operation_id="alert_delete", description="Removing what is not there is a success, so a retry is safe.")
    def delete(self, request, pk: int):
        self.throttle_classes = [WriteThrottle]
        self.check_throttles(request)
        HomeAlert.objects.filter(pk=pk, telegram_user_id=request.user.telegram_user_id).delete()
        return Response(status=204)


class AlertListingsView(FeatureView):
    feature = "alerts_api_on"

    @doc(
        "feature_off",
        "alert_gone",
        "validation_failed",
        me=True,
        parameters=[SavedQuerySerializer],
        responses={200: AlertListingsSerializer},
        operation_id="alert_listings",
        description="F16#6: what matched since it was last viewed, newest first. Reading changes nothing; POST viewed/ clears the count.",
    )
    def get(self, request, pk: int):
        query = SavedQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        rows = alert_store.new_listings(_own_alert(request, pk))
        start = int(query.validated_data.get("cursor") or 0)
        end = start + SAVED_PAGE
        channels, storage = city_channels(), get_storage()
        return Response(
            {
                "results": [public_card(listing, channels, storage=storage) for listing in rows[start:end]],
                "total": len(rows),
                "next_cursor": str(end) if end < len(rows) else None,
            }
        )


class AlertViewedView(FeatureView):
    feature = "alerts_api_on"

    @doc("feature_off", "alert_gone", "rate_limited", me=True, request=None, responses={204: None}, operation_id="alert_viewed", description="Clears `new_count`. A retry is safe.")
    def post(self, request, pk: int):
        self.throttle_classes = [WriteThrottle]
        self.check_throttles(request)
        alert = _own_alert(request, pk)
        alert.viewed_at = timezone.now()
        alert.save(update_fields=["viewed_at"])
        return Response(status=204)


class NotificationsView(FeatureView):
    feature = "account_api_on"

    @doc(
        "feature_off",
        "validation_failed",
        me=True,
        parameters=[SavedQuerySerializer],
        responses={200: NotificationListSerializer},
        operation_id="notifications_list",
        description="F17: newest first, kept for 90 days.",
    )
    def get(self, request):
        query = SavedQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        start = int(query.validated_data.get("cursor") or 0)
        end = start + NOTIFICATION_PAGE
        rows = HomeNotification.objects.filter(user=request.user, created_at__gte=timezone.now() - timedelta(days=HomeNotification.RETENTION_DAYS))
        page = list(rows.select_related("listing__post", "listing__canonical__post")[start : end + 1])
        return Response(
            {
                "results": [inbox.payload(row) for row in page[:NOTIFICATION_PAGE]],
                "unread": inbox.unread(request.user),
                "next_cursor": str(end) if len(page) > NOTIFICATION_PAGE else None,
            }
        )


class NotificationsReadView(FeatureView):
    feature = "account_api_on"

    @doc(
        "feature_off",
        "validation_failed",
        "bad_request",
        "rate_limited",
        me=True,
        request=NotificationReadRequestSerializer,
        responses={200: NotificationReadSerializer},
        operation_id="notifications_read",
        description="Marks some or all as read. Ids that are not this person's are ignored, and a retry is safe.",
    )
    def post(self, request):
        self.throttle_classes = [WriteThrottle]
        self.check_throttles(request)
        body = NotificationReadRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        rows = HomeNotification.objects.filter(user=request.user, read_at__isnull=True)
        if "ids" in body.validated_data:
            rows = rows.filter(pk__in=body.validated_data["ids"])
        rows.update(read_at=timezone.now())
        return Response({"unread": inbox.unread(request.user)})


class MineView(FeatureView):
    feature = "posting_api_on"

    @doc(
        "feature_off",
        "validation_failed",
        me=True,
        parameters=[SavedQuerySerializer],
        responses={200: MineListSerializer},
        operation_id="mine_list",
        description="F20: this person's listings, newest first: posted in the app, and crawled posts verified as theirs.",
    )
    def get(self, request):
        query = SavedQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        rows = user_listings(request.user).exclude(status=ListingStatus.REMOVED)
        start = int(query.validated_data.get("cursor") or 0)
        end = start + SAVED_PAGE
        channels, storage = city_channels(), get_storage()
        return Response(
            {
                "results": [owner_posts.mine_row(listing, channels, storage) for listing in rows[start:end]],
                "total": rows.count(),
                "next_cursor": str(end) if rows[end : end + 1].exists() else None,
            }
        )

    @doc(
        "feature_off",
        "duplicate_listing",
        "photo_gone",
        "post_cap",
        "validation_failed",
        "bad_request",
        "rate_limited",
        me=True,
        parameters=[IDEMPOTENCY_KEY],
        request=PostCreateRequestSerializer,
        responses={200: PostResultSerializer, 201: PostResultSerializer},
        operation_id="post_create",
        description=(
            "F18, F19. A retry with the same key answers 200 with the first result. `fields` under `validation_failed` names each problem: "
            "`too_short` or `contact_in_text` on description, `required` on any field a post needs, `no_contact` on phone, `invalid_choice` on country."
        ),
    )
    def post(self, request):
        self.throttle_classes = [WriteThrottle]
        self.check_throttles(request)
        key = (request.headers.get("Idempotency-Key") or "").strip()
        if not key or len(key) > 64:
            raise ApiError("bad_request", "Send one Idempotency-Key of at most 64 characters.")
        body = PostCreateRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        with _post_errors():
            result, created = owner_posts.create_post(request.user, key, _post_form(body.validated_data), locale=_prefs_for(request.user).locale, countries=countries())
        return Response(result, status=201 if created else 200)


class MineFormView(FeatureView):
    feature = "posting_api_on"

    @doc("feature_off", "listing_gone", me=True, responses={200: MineFormSerializer}, operation_id="listing_form", description="F20: opens the edit form. Owner only, since it holds the phone.")
    def get(self, request, pk: int):
        with _post_errors():
            return Response(MineFormSerializer(owner_posts.form_of(request.user, owner_posts.own_listing(request.user, pk))).data)


class MineStatusView(FeatureView):
    feature = "posting_api_on"

    @doc(
        "feature_off",
        "listing_gone",
        "status_not_allowed",
        "validation_failed",
        "bad_request",
        "rate_limited",
        me=True,
        request=ListingStatusRequestSerializer,
        responses={200: MineRowSerializer},
        operation_id="listing_status",
        description="F20#3, F20#11: close, its undo, and renew. Closing edits the channel post, and restore brings it back.",
    )
    def post(self, request, pk: int):
        self.throttle_classes = [WriteThrottle]
        self.check_throttles(request)
        body = ListingStatusRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        with _post_errors():
            listing = owner_posts.own_listing(request.user, pk)
            owner_posts.change_status(listing, body.validated_data["action"])
        listing.refresh_from_db()
        return Response(owner_posts.mine_row(listing, city_channels(), get_storage()))


class PhotoUploadView(FeatureView):
    feature = "posting_api_on"

    @doc(
        "feature_off",
        "not_image",
        "too_big",
        "too_many",
        "upload_unavailable",
        "validation_failed",
        "bad_request",
        "rate_limited",
        me=True,
        request=PhotoUploadRequestSerializer,
        responses={201: PhotoUploadTicketSerializer},
        operation_id="photo_upload",
        description="Step 1 of 2: a signed URL to PUT one file straight to storage, so the file skips the API. At most 6 photos, each at most 3 MB.",
    )
    def post(self, request):
        self.throttle_classes = [WriteThrottle]
        self.check_throttles(request)
        body = PhotoUploadRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        with _post_errors():
            row, url, headers = uploads.start(request.user, body.validated_data["content_type"], body.validated_data["size"])
        return Response({"photo_id": row.pk, "upload_url": url, "headers": headers, "expires_in": UPLOAD_URL_SECONDS}, status=201)


class PhotoView(FeatureView):
    feature = "posting_api_on"

    @doc(
        "feature_off",
        "photo_gone",
        "not_image",
        "too_big",
        "upload_failed",
        "upload_unavailable",
        "rate_limited",
        me=True,
        request=None,
        responses={200: UploadedPhotoSerializer},
        operation_id="photo_confirm",
        description="Step 2 of 2, after the PUT: checks the bytes, then makes the resized image and thumbnail. Each photo answers for itself, so one bad file fails only its tile (F18#30). A retry is safe.",
    )
    def post(self, request, photo_id):
        self.throttle_classes = [WriteThrottle]
        self.check_throttles(request)
        with _post_errors():
            return Response(uploads.payload(uploads.confirm(request.user, photo_id)))

    @doc("feature_off", "rate_limited", me=True, request=None, responses={204: None}, operation_id="photo_delete", description="Removes an upload not posted yet. Removing what is not there is a success.")
    def delete(self, request, photo_id):
        self.throttle_classes = [WriteThrottle]
        self.check_throttles(request)
        uploads.discard(request.user, photo_id)
        return Response(status=204)


class PostParseView(FeatureView):
    feature = "smart_posting_on"

    def initial(self, request, *args, **kwargs) -> None:
        super().initial(request, *args, **kwargs)
        _posting_open(request)

    @doc(
        "feature_off",
        "parse_cap",
        "parse_unavailable",
        "validation_failed",
        "bad_request",
        "rate_limited",
        me=True,
        request=PostParseRequestSerializer,
        responses={200: PostParseSerializer},
        operation_id="post_parse",
        description="F18#2 to #3: reads pasted text into the form, and saves nothing. `parse_cap` and `parse_unavailable` both open the manual form (F18#10), with the text kept. `too_short` on text: F18#18.",
    )
    def post(self, request):
        self.throttle_classes = [WriteThrottle]
        self.check_throttles(request)
        body = PostParseRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        country = data["country"].upper()
        if country not in countries():
            raise ValidationError({"country": ErrorDetail("Unknown country.", code="invalid_choice")})
        try:
            return Response(smart_post.parse(request.user, text=data["text"], kind=data["kind"], country=country, city=data.get("city") or ""))
        except smart_post.TextTooShort as exc:
            raise ValidationError({"text": ErrorDetail("Too short.", code="too_short")}) from exc
        except smart_post.ParseCapReached as exc:
            raise ApiError("parse_cap") from exc
        except smart_post.ParseUnavailable as exc:
            raise ApiError("parse_unavailable") from exc


class NotFoundView(V1View):
    """Any other path under /api/v1/ still answers with a code, never Django's HTML 404."""

    obeys_maintenance = False

    def initial(self, request, *args, **kwargs) -> None:
        raise ApiError("not_found")

    @extend_schema(exclude=True)
    def get(self, request, *args, **kwargs):
        raise ApiError("not_found")



class OutreachLandingView(MeView):
    @doc(
        "validation_failed",
        "bad_request",
        me=True,
        request=OutreachLandingRequestSerializer,
        responses={200: OutreachLandingSerializer},
        operation_id="outreach_landing",
        description="After a link from an outreach message (`src` starting with `o-`): what to offer this person. Safe to retry.",
    )
    def post(self, request):
        from outreach.housing import landing

        body = OutreachLandingRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return Response(landing(body.validated_data["src"], request.user))


class OutreachAlertConfirmView(MeView):
    @doc("not_found", "rate_limited", me=True, request=None, responses={200: OutreachAlertSerializer}, operation_id="outreach_alert_confirm", description="The one tap that turns on the alert made from their request. Safe to retry.")
    def post(self, request, pk: int):
        from home.alerts import search
        from outreach.housing import confirm_alert

        self.throttle_classes = [WriteThrottle]
        self.check_throttles(request)
        alert = confirm_alert(pk, request.user)
        if alert is None:
            raise ApiError("not_found")
        return Response({"id": alert.pk, "active": alert.active, "city": alert.city, "max_price": float(alert.max_price) if alert.max_price is not None else None, "currency": alert.currency, "match_count": len(search(alert))})


class OutreachWriteAccessView(MeView):
    @doc(me=True, request=None, responses={204: None}, operation_id="outreach_write_access", description="Telegram's requestWriteAccess answered yes: the housing bot may write to this person.")
    def post(self, request):
        from outreach.housing import write_access_given

        write_access_given(request.user)
        return Response(status=204)
