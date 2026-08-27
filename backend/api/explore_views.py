from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from api.errors import raise_api_validation
from api.permissions import IsTelegramUser
from api.serializers import MatchSerializer, OpenRequestSerializer
from item_requests.explore import LIST_LIMIT, open_requests_queryset
from item_requests.models import ItemRequest
from matching.manual import propose_user_match
from matching.models import Match


class ExploreViewSet(viewsets.GenericViewSet):
    permission_classes = [IsTelegramUser]
    serializer_class = OpenRequestSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        params = self.request.query_params if getattr(self, "action", None) == "list" else None
        return open_requests_queryset(self.request.user, params)

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
