from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from api.errors import raise_api_validation
from api.permissions import IsTelegramUser
from api.serializers import SupportTicketSerializer
from support.services import (
    add_user_message,
    close_ticket_by_user,
    create_ticket,
    tickets_for_user,
)


class SupportTicketViewSet(viewsets.GenericViewSet):
    permission_classes = [IsTelegramUser]
    serializer_class = SupportTicketSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        return tickets_for_user(self.request.user)

    def list(self, request: Request) -> Response:
        return Response(SupportTicketSerializer(self.get_queryset(), many=True).data)

    def retrieve(self, request: Request, pk: str | None = None) -> Response:
        return Response(SupportTicketSerializer(self.get_object()).data)

    def create(self, request: Request) -> Response:
        try:
            ticket = create_ticket(request.user, request.data)
        except DjangoValidationError as exc:
            raise_api_validation(exc)
        ticket = self.get_queryset().get(pk=ticket.pk)
        return Response(SupportTicketSerializer(ticket).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def messages(self, request: Request, pk: str | None = None) -> Response:
        ticket = self.get_object()
        try:
            add_user_message(ticket, request.user, request.data)
        except DjangoValidationError as exc:
            raise_api_validation(exc)
        ticket = self.get_queryset().get(pk=ticket.pk)
        return Response(SupportTicketSerializer(ticket).data)

    @action(detail=True, methods=["post"])
    def close(self, request: Request, pk: str | None = None) -> Response:
        ticket = self.get_object()
        try:
            ticket = close_ticket_by_user(ticket, request.user)
        except DjangoValidationError as exc:
            raise_api_validation(exc)
        ticket = self.get_queryset().get(pk=ticket.pk)
        return Response(SupportTicketSerializer(ticket).data)
