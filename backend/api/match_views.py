from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from api.errors import raise_api_validation
from api.permissions import IsTelegramUser
from api.serializers import MatchSerializer
from matching.acceptance import accept_match, reject_match
from matching.completion import complete_match, rate_match
from matching.models import USER_MATCH_STATUSES, matches_for_user


class MatchViewSet(viewsets.GenericViewSet):
    permission_classes = [IsTelegramUser]
    serializer_class = MatchSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        return (
            matches_for_user(self.request.user)
            .filter(status__in=USER_MATCH_STATUSES)
            .select_related(
                "demand_request",
                "demand_request__user",
                "supply_request",
                "supply_request__user",
            )
            .prefetch_related(
                "demand_request__item_categories",
                "supply_request__item_categories",
                "ratings",
            )
        )

    def list(self, request: Request) -> Response:
        serializer = MatchSerializer(self.get_queryset(), many=True, context={"request": request})
        return Response(serializer.data)

    def retrieve(self, request: Request, pk: str | None = None) -> Response:
        match = self.get_object()
        return Response(MatchSerializer(match, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def accept(self, request: Request, pk: str | None = None) -> Response:
        match = self.get_object()
        try:
            match = accept_match(match, request.user)
        except DjangoValidationError as exc:
            raise_api_validation(exc)
        match = self.get_queryset().get(pk=match.pk)
        return Response(MatchSerializer(match, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def reject(self, request: Request, pk: str | None = None) -> Response:
        match = self.get_object()
        try:
            match = reject_match(match, request.user)
        except DjangoValidationError as exc:
            raise_api_validation(exc)
        return Response(MatchSerializer(match, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def complete(self, request: Request, pk: str | None = None) -> Response:
        match = self.get_object()
        try:
            match = complete_match(match, request.user)
        except DjangoValidationError as exc:
            raise_api_validation(exc)
        match = self.get_queryset().get(pk=match.pk)
        return Response(MatchSerializer(match, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def rate(self, request: Request, pk: str | None = None) -> Response:
        match = self.get_object()
        try:
            rate_match(match, request.user, request.data.get("score"), request.data.get("comment") or "")
        except DjangoValidationError as exc:
            raise_api_validation(exc)
        match = self.get_queryset().get(pk=match.pk)
        return Response(MatchSerializer(match, context={"request": request}).data)
