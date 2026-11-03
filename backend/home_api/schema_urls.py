"""The URLs openapi.yaml is built from: the live API, plus the R2 snapshot files, which no Django view serves."""

from __future__ import annotations

from django.urls import include, path
from rest_framework.views import APIView

from home_api.schema import doc
from home_api.serializers import ManifestSerializer, SnapshotFileSerializer


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
        served="Once snapshots_on is on. A rewrite to R2, not a Django view, on this origin. Immutable.",
        responses={200: SnapshotFileSerializer},
        operation_id="snapshot_file",
        description="All public listings of one city, pre-compressed with Content-Encoding. A new version is a new file.",
    )
    def get(self, request, city: str, version: str):
        raise NotImplementedError


urlpatterns = [
    path("api/v1/", include("home_api.urls")),
    path("feeds/manifest.json", ManifestDoc.as_view()),
    path("feeds/<str:city>.<str:version>.json", SnapshotDoc.as_view()),
]
