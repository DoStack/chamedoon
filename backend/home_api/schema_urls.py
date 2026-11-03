"""The URLs openapi.yaml is built from: the live API, the R2 snapshot files, which no Django view serves, and phases P3 and P4 until each goes live."""

from __future__ import annotations

from django.urls import include, path
from drf_spectacular.utils import OpenApiParameter
from rest_framework.views import APIView

from api.authentication import TelegramJWTAuthentication
from api.permissions import IsTelegramUser
from home_api.schema import doc
from home_api.serializers import (
    AccountSerializer,
    AlertCreateRequestSerializer,
    AlertListingsSerializer,
    AlertListSerializer,
    AlertRowSerializer,
    AlertSaveSerializer,
    AlertUpdateRequestSerializer,
    ListingEditRequestSerializer,
    ListingStatusRequestSerializer,
    ManifestSerializer,
    MineFormSerializer,
    MineListSerializer,
    MineRowSerializer,
    NotificationListSerializer,
    NotificationReadRequestSerializer,
    NotificationReadSerializer,
    PhotoUploadRequestSerializer,
    PhotoUploadTicketSerializer,
    PostCreateRequestSerializer,
    PostParseRequestSerializer,
    PostParseSerializer,
    PostResultSerializer,
    SavedQuerySerializer,
    SnapshotFileSerializer,
    UploadedPhotoSerializer,
)

SERVED_P3 = "P3. Not routed yet, so it answers `not_found` until its PR; then `feature_off` until the flag is on."
SERVED_P4 = "P4. Not routed yet, so it answers `not_found` until its PR; then `feature_off` until the flag is on."
SERVED_SMART = "Smart posting. Not routed yet, so it answers `not_found` until its PR; then `feature_off` until the flag is on."
IDEMPOTENCY_KEY = OpenApiParameter(
    "Idempotency-Key", str, OpenApiParameter.HEADER, required=True, description="One per draft. The same key again answers the first result and posts nothing new."
)


class ManifestDoc(APIView):
    authentication_classes: list = []
    permission_classes: list = []

    @doc(
        maintenance=False,
        served="Once snapshots_on is on. A rewrite to R2, not a Django view, on this origin. Short cache.",
        responses={200: ManifestSerializer},
        operation_id="snapshot_manifest",
        description="Lists each city's current snapshot file. On failure use /api/v1/public/feed/.",
    )
    def get(self, request):
        raise NotImplementedError


class SnapshotDoc(APIView):
    authentication_classes: list = []
    permission_classes: list = []

    @doc(
        maintenance=False,
        served="Once snapshots_on is on. A rewrite to R2, not a Django view, on this origin. A version never changes; cached for a day.",
        responses={200: SnapshotFileSerializer},
        operation_id="snapshot_file",
        description="All public listings of one city, pre-compressed with Content-Encoding. A new version is a new file.",
    )
    def get(self, request, city_key: str, version: str):
        raise NotImplementedError


class MeDoc(APIView):
    """A /me operation that is in the contract and not live yet."""

    authentication_classes = [TelegramJWTAuthentication]
    permission_classes = [IsTelegramUser]


class AccountDoc(MeDoc):
    @doc("feature_off", me=True, served=SERVED_P3, responses={200: AccountSerializer}, operation_id="account_read", description="F21 and the two badges.")
    def get(self, request):
        raise NotImplementedError


class AlertsDoc(MeDoc):
    @doc("feature_off", me=True, served=SERVED_P3, responses={200: AlertListSerializer}, operation_id="alerts_list", description="F16: newest first, with no paging, since the cap keeps it short.")
    def get(self, request):
        raise NotImplementedError

    @doc(
        "feature_off",
        "alert_cap",
        "validation_failed",
        "bad_request",
        "rate_limited",
        me=True,
        served=SERVED_P3,
        request=AlertCreateRequestSerializer,
        responses={200: AlertSaveSerializer, 201: AlertSaveSerializer},
        operation_id="alert_create",
        description="F15. The same search finds the same row, so a retry is safe and no idempotency key is needed. Saved with zero matches too (F15#6). `city` under `fields`: no such city.",
    )
    def post(self, request):
        raise NotImplementedError


class AlertDoc(MeDoc):
    @doc("feature_off", "alert_gone", me=True, served=SERVED_P3, responses={200: AlertRowSerializer}, operation_id="alert_read", description="F16#12 when it is gone.")
    def get(self, request, pk: int):
        raise NotImplementedError

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
        served=SERVED_P3,
        request=AlertUpdateRequestSerializer,
        responses={200: AlertRowSerializer},
        operation_id="alert_update",
        description="Saves only the fields sent, so a retry is safe. `alert_state` works on a wanted alert; `filters` and `delivery` answer `alert_not_editable`. Turning on at the cap: `alert_cap`.",
    )
    def patch(self, request, pk: int):
        raise NotImplementedError

    @doc("feature_off", "rate_limited", me=True, served=SERVED_P3, request=None, responses={204: None}, operation_id="alert_delete", description="Removing what is not there is a success, so a retry is safe.")
    def delete(self, request, pk: int):
        raise NotImplementedError


class AlertListingsDoc(MeDoc):
    @doc(
        "feature_off",
        "alert_gone",
        "validation_failed",
        me=True,
        served=SERVED_P3,
        parameters=[SavedQuerySerializer],
        responses={200: AlertListingsSerializer},
        operation_id="alert_listings",
        description="F16#6: what matched since it was last viewed, newest first. Reading changes nothing; POST viewed/ clears the count.",
    )
    def get(self, request, pk: int):
        raise NotImplementedError


class AlertViewedDoc(MeDoc):
    @doc("feature_off", "alert_gone", "rate_limited", me=True, served=SERVED_P3, request=None, responses={204: None}, operation_id="alert_viewed", description="Clears `new_count`. A retry is safe.")
    def post(self, request, pk: int):
        raise NotImplementedError


class NotificationsDoc(MeDoc):
    @doc(
        "feature_off",
        "validation_failed",
        me=True,
        served=SERVED_P3,
        parameters=[SavedQuerySerializer],
        responses={200: NotificationListSerializer},
        operation_id="notifications_list",
        description="F17: newest first, kept for 90 days.",
    )
    def get(self, request):
        raise NotImplementedError


class NotificationsReadDoc(MeDoc):
    @doc(
        "feature_off",
        "validation_failed",
        "bad_request",
        "rate_limited",
        me=True,
        served=SERVED_P3,
        request=NotificationReadRequestSerializer,
        responses={200: NotificationReadSerializer},
        operation_id="notifications_read",
        description="Marks some or all as read. Ids that are not this person's are ignored, and a retry is safe.",
    )
    def post(self, request):
        raise NotImplementedError


class MineDoc(MeDoc):
    @doc(
        "feature_off",
        "validation_failed",
        me=True,
        served=SERVED_P4,
        parameters=[SavedQuerySerializer],
        responses={200: MineListSerializer},
        operation_id="mine_list",
        description="F20: this person's listings posted in the app, newest first. Crawled posts are not listed.",
    )
    def get(self, request):
        raise NotImplementedError

    @doc(
        "feature_off",
        "duplicate_listing",
        "photo_gone",
        "post_cap",
        "validation_failed",
        "bad_request",
        "rate_limited",
        me=True,
        served=SERVED_P4,
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
        raise NotImplementedError


class MineListingDoc(MeDoc):
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
        served=SERVED_P4,
        request=ListingEditRequestSerializer,
        responses={200: PostResultSerializer},
        operation_id="listing_edit",
        description="F20#2. The whole form again, through the same checks as a post. It goes back through review; the old version stays up until that passes. A retry is safe: the second call sees the same values.",
    )
    def patch(self, request, pk: int):
        raise NotImplementedError

    @doc(
        "feature_off",
        "rate_limited",
        me=True,
        served=SERVED_P4,
        request=None,
        responses={204: None},
        operation_id="listing_delete",
        description="F20#6. The channel post changes to closed, and the old link answers `listing_gone`. Removing what is not there is a success, so a retry is safe.",
    )
    def delete(self, request, pk: int):
        raise NotImplementedError


class MineFormDoc(MeDoc):
    @doc("feature_off", "listing_gone", me=True, served=SERVED_P4, responses={200: MineFormSerializer}, operation_id="listing_form", description="F20: opens the edit form. Owner only, since it holds the phone.")
    def get(self, request, pk: int):
        raise NotImplementedError


class MineStatusDoc(MeDoc):
    @doc(
        "feature_off",
        "listing_gone",
        "status_not_allowed",
        "validation_failed",
        "bad_request",
        "rate_limited",
        me=True,
        served=SERVED_P4,
        request=ListingStatusRequestSerializer,
        responses={200: MineRowSerializer},
        operation_id="listing_status",
        description="F20#3, F20#11: close, its undo, and renew. Closing edits the channel post, and restore brings it back.",
    )
    def post(self, request, pk: int):
        raise NotImplementedError


class PostParseDoc(MeDoc):
    @doc(
        "feature_off",
        "parse_cap",
        "parse_unavailable",
        "validation_failed",
        "bad_request",
        "rate_limited",
        me=True,
        served=SERVED_SMART,
        request=PostParseRequestSerializer,
        responses={200: PostParseSerializer},
        operation_id="post_parse",
        description="F18#2 to #3: reads pasted text into the form, and saves nothing. `parse_cap` and `parse_unavailable` both open the manual form (F18#10), with the text kept.",
    )
    def post(self, request):
        raise NotImplementedError


class PhotoUploadDoc(MeDoc):
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
        served=SERVED_P4,
        request=PhotoUploadRequestSerializer,
        responses={201: PhotoUploadTicketSerializer},
        operation_id="photo_upload",
        description="Step 1 of 2: a signed URL to PUT one file straight to storage, so the file skips the API. At most 6 photos, each at most 3 MB.",
    )
    def post(self, request):
        raise NotImplementedError


class PhotoDoc(MeDoc):
    @doc(
        "feature_off",
        "photo_gone",
        "not_image",
        "too_big",
        "upload_failed",
        "upload_unavailable",
        "rate_limited",
        me=True,
        served=SERVED_P4,
        request=None,
        responses={200: UploadedPhotoSerializer},
        operation_id="photo_confirm",
        description="Step 2 of 2, after the PUT: checks the bytes, then makes the resized image and thumbnail. Each photo answers for itself, so one bad file fails only its tile (F18#30). A retry is safe.",
    )
    def post(self, request, photo_id: str):
        raise NotImplementedError

    @doc("feature_off", "rate_limited", me=True, served=SERVED_P4, request=None, responses={204: None}, operation_id="photo_delete", description="Removes an upload not posted yet. Removing what is not there is a success.")
    def delete(self, request, photo_id: str):
        raise NotImplementedError


urlpatterns = [
    path("api/v1/", include("home_api.urls")),
    path("api/v1/me/account/", AccountDoc.as_view()),
    path("api/v1/me/alerts/", AlertsDoc.as_view()),
    path("api/v1/me/alerts/<int:pk>/", AlertDoc.as_view()),
    path("api/v1/me/alerts/<int:pk>/listings/", AlertListingsDoc.as_view()),
    path("api/v1/me/alerts/<int:pk>/viewed/", AlertViewedDoc.as_view()),
    path("api/v1/me/notifications/", NotificationsDoc.as_view()),
    path("api/v1/me/notifications/read/", NotificationsReadDoc.as_view()),
    path("api/v1/me/listings/", MineDoc.as_view()),
    path("api/v1/me/listings/<int:pk>/", MineListingDoc.as_view()),
    path("api/v1/me/listings/<int:pk>/form/", MineFormDoc.as_view()),
    path("api/v1/me/listings/<int:pk>/status/", MineStatusDoc.as_view()),
    path("api/v1/me/posts/parse/", PostParseDoc.as_view()),
    path("api/v1/me/photos/", PhotoUploadDoc.as_view()),
    path("api/v1/me/photos/<uuid:photo_id>/", PhotoDoc.as_view()),
    path("feeds/manifest.json", ManifestDoc.as_view()),
    path("feeds/<str:city_key>.<str:version>.json", SnapshotDoc.as_view()),
]
