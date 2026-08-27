from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from api.errors import raise_api_validation
from api.permissions import IsTelegramUser
from api.serializers import MatchSerializer, OpenRequestSerializer
from item_requests.models import ItemRequest, RequestStatus, RequestType
from item_requests.services import expire_user_requests
from matching.manual import propose_user_match
from matching.models import Match

LIST_LIMIT = 100


class ExploreViewSet(viewsets.GenericViewSet):
    permission_classes = [IsTelegramUser]
    serializer_class = OpenRequestSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        expire_user_requests(self.request.user)
        queryset = (
            ItemRequest.objects.filter(status=RequestStatus.ACTIVE, expires_at__gt=timezone.now())
            .exclude(user=self.request.user)
            .select_related("user")
            .prefetch_related("item_categories", "excluded_categories")
        )
        if getattr(self, "action", None) == "list":
            queryset = self._apply_filters(queryset)
        return queryset.distinct()

    def _apply_filters(self, queryset):
        params = self.request.query_params
        request_type = params.get("type")
        if request_type in RequestType.values:
            queryset = queryset.filter(type=request_type)

        origin_country = (params.get("origin_country") or "").strip().upper()
        if origin_country:
            queryset = queryset.filter(origin_country=origin_country)
        origin_city = (params.get("origin_city") or "").strip()
        if origin_city:
            queryset = queryset.filter(origin_city=origin_city)

        destination_country = (params.get("destination_country") or "").strip().upper()
        if destination_country:
            queryset = queryset.filter(destination_country=destination_country)
        destination_city = (params.get("destination_city") or "").strip()
        if destination_city:
            queryset = queryset.filter(destination_city=destination_city)

        category = (params.get("category") or "").strip()
        if category:
            queryset = queryset.filter(item_categories__code=category)

        date_from = parse_date(params.get("date_from") or "")
        if date_from:
            queryset = queryset.filter(date_to__gte=date_from)
        date_to = parse_date(params.get("date_to") or "")
        if date_to:
            queryset = queryset.filter(date_from__lte=date_to)

        return queryset

    def list(self, request: Request) -> Response:
        queryset = self.get_queryset()[:LIST_LIMIT]
        return Response(OpenRequestSerializer(queryset, many=True).data)

    @action(detail=True, methods=["post"])
    def connect(self, request: Request, pk: str | None = None) -> Response:
        other = self.get_object()
        try:
            mine = self._own_request(request)
            match = propose_user_match(request.user, other, mine)
        except DjangoValidationError as exc:
            raise_api_validation(exc)
        match = (
            Match.objects.select_related(
                "demand_request",
                "demand_request__user",
                "supply_request",
                "supply_request__user",
            )
            .prefetch_related(
                "demand_request__item_categories",
                "supply_request__item_categories",
            )
            .get(pk=match.pk)
        )
        return Response(MatchSerializer(match, context={"request": request}).data, status=status.HTTP_200_OK)

    def _own_request(self, request: Request) -> ItemRequest | None:
        raw = request.data.get("my_request_id") if isinstance(request.data, dict) else None
        if raw in (None, ""):
            return None
        try:
            return ItemRequest.objects.get(pk=int(raw))
        except (TypeError, ValueError, ItemRequest.DoesNotExist):
            raise DjangoValidationError({"my_request_id": "Request not found."})
