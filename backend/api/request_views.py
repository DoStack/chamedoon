from django.db.models import Count, Q
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response

from api.errors import raise_api_validation
from api.permissions import IsTelegramUser
from api.serializers import CategorySerializer, CountrySerializer, ItemRequestSerializer
from item_requests.models import Category, Country, ItemRequest
from matching.models import VISIBLE_MATCH_STATUSES
from item_requests.services import (
    cancel_item_request,
    create_item_request,
    expire_user_requests,
    update_item_request,
)


@api_view(["GET"])
@permission_classes([AllowAny])
def categories(_request: Request) -> Response:
    queryset = Category.objects.filter(is_active=True)
    return Response(CategorySerializer(queryset, many=True).data)


@api_view(["GET"])
@permission_classes([AllowAny])
def locations(_request: Request) -> Response:
    queryset = Country.objects.filter(is_active=True).prefetch_related("cities")
    return Response({"countries": CountrySerializer(queryset, many=True).data})


class RequestViewSet(viewsets.GenericViewSet):
    permission_classes = [IsTelegramUser]
    serializer_class = ItemRequestSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        expire_user_requests(self.request.user)
        return (
            ItemRequest.objects.filter(user=self.request.user)
            .prefetch_related("item_categories", "excluded_categories")
            .annotate(
                _demand_match_count=Count(
                    "demand_matches",
                    filter=Q(demand_matches__status__in=VISIBLE_MATCH_STATUSES),
                    distinct=True,
                ),
                _supply_match_count=Count(
                    "supply_matches",
                    filter=Q(supply_matches__status__in=VISIBLE_MATCH_STATUSES),
                    distinct=True,
                ),
            )
        )

    def list(self, request: Request) -> Response:
        serializer = ItemRequestSerializer(self.get_queryset(), many=True)
        return Response(serializer.data)

    def retrieve(self, request: Request, pk: str | None = None) -> Response:
        item_request = self.get_object()
        return Response(ItemRequestSerializer(item_request).data)

    def create(self, request: Request) -> Response:
        try:
            item_request = create_item_request(request.user, request.data)
        except DjangoValidationError as exc:
            raise_api_validation(exc)
        item_request = self.get_queryset().get(pk=item_request.pk)
        return Response(ItemRequestSerializer(item_request).data, status=status.HTTP_201_CREATED)

    def partial_update(self, request: Request, pk: str | None = None) -> Response:
        item_request = self.get_object()
        try:
            item_request = update_item_request(item_request, request.data)
        except DjangoValidationError as exc:
            raise_api_validation(exc)
        item_request = self.get_queryset().get(pk=item_request.pk)
        return Response(ItemRequestSerializer(item_request).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request: Request, pk: str | None = None) -> Response:
        item_request = self.get_object()
        try:
            item_request = cancel_item_request(item_request)
        except DjangoValidationError as exc:
            raise_api_validation(exc)
        return Response(ItemRequestSerializer(item_request).data)
