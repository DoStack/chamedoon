"""F08's endpoints. With the flag off each answers `feature_off`, and the client keeps the plain text search of F04."""

from __future__ import annotations

from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from aisearch import service
from aisearch.serializers import (
    SearchClearedSerializer,
    SearchParseRequestSerializer,
    SearchParseSerializer,
    SearchRecentSerializer,
)
from home import conf
from home_api.errors import ApiError
from home_api.schema import doc
from home_api.views import MeView, WriteThrottle


class SearchThrottle(UserRateThrottle):
    scope = "home_search"
    rate = "120/min"


class SearchView(MeView):
    def initial(self, request, *args, **kwargs) -> None:
        super().initial(request, *args, **kwargs)
        if not conf.row().ai_search_enabled:
            raise ApiError("feature_off", "Text search is off.")


class SearchParseView(SearchView):
    throttle_classes = [SearchThrottle]

    @doc(
        "validation_failed",
        "rate_limited",
        "feature_off",
        me=True,
        request=SearchParseRequestSerializer,
        responses={200: SearchParseSerializer},
        operation_id="search_parse",
        description="F08: a query as the list screen's filters. Rules first, then the model within 6 seconds, and the rules' answer on any failure. Repeats are safe.",
    )
    def post(self, request):
        body = SearchParseRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        answer = service.parse(data["text"], country=data.get("country", ""), city=data.get("city", ""), telegram_user_id=request.user.telegram_user_id, rules_only=data["rules_only"])
        return Response(answer)


class SearchRecentView(SearchView):
    def initial(self, request, *args, **kwargs) -> None:
        if request.method == "DELETE":
            self.throttle_classes = [WriteThrottle]
        super().initial(request, *args, **kwargs)

    @doc("feature_off", me=True, responses={200: SearchRecentSerializer}, operation_id="search_recent", description="F08#1: this person's latest searches.")
    def get(self, request):
        return Response({"results": service.recent(request.user.telegram_user_id)})

    @doc("feature_off", "rate_limited", me=True, responses={200: SearchClearedSerializer}, operation_id="search_recent_clear", description="F08#11: clears the list. Keep `cleared_at` for the undo.")
    def delete(self, request):
        return Response({"cleared_at": service.clear_recent(request.user.telegram_user_id)})


class SearchRestoreView(SearchView):
    throttle_classes = [WriteThrottle]

    @doc(
        "feature_off",
        "validation_failed",
        "rate_limited",
        me=True,
        request=SearchClearedSerializer,
        responses={200: SearchRecentSerializer},
        operation_id="search_recent_restore",
        description="F08#11: the undo. Repeats are safe.",
    )
    def post(self, request):
        body = SearchClearedSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        service.restore_recent(request.user.telegram_user_id, body.validated_data["cleared_at"])
        return Response({"results": service.recent(request.user.telegram_user_id)})
