"""The URLs openapi.yaml is built from: the live API, the R2 snapshot files, which no Django view serves, and phases P3 and P4 until each goes live."""

from __future__ import annotations

from django.urls import include, path
from rest_framework.views import APIView

from api.authentication import TelegramJWTAuthentication
from api.permissions import IsTelegramUser
from home_api.schema import doc
from home_api.serializers import (
    ManifestSerializer,
    PostParseRequestSerializer,
    PostParseSerializer,
    SnapshotFileSerializer,
)

SERVED_SMART = "Smart posting. Not routed yet, so it answers `not_found` until its PR; then `feature_off` until the flag is on."


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


urlpatterns = [
    path("api/v1/", include("home_api.urls")),
    path("api/v1/me/posts/parse/", PostParseDoc.as_view()),
    path("feeds/manifest.json", ManifestDoc.as_view()),
    path("feeds/<str:city_key>.<str:version>.json", SnapshotDoc.as_view()),
]
