from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.request import Request
from rest_framework.response import Response

from api.errors import raise_api_validation
from api.permissions import IsBotService
from api.serializers import ItemRequestSerializer, UserSerializer
from item_requests.models import ItemRequest, RequestStatus
from item_requests.services import create_item_request
from matching.models import VISIBLE_MATCH_STATUSES, Match
from users.models import User
from users.services import upsert_telegram_user
from users.telegram import TelegramIdentity


@api_view(["POST"])
@authentication_classes([])
@permission_classes([IsBotService])
def bot_sync_user(request: Request) -> Response:
    data = request.data if isinstance(request.data, dict) else {}
    try:
        telegram_user_id = int(data["telegram_user_id"])
    except (KeyError, TypeError, ValueError):
        return Response({"detail": "telegram_user_id is required"}, status=status.HTTP_400_BAD_REQUEST)
    first_name = str(data.get("first_name") or "").strip()
    if not first_name:
        return Response({"detail": "first_name is required"}, status=status.HTTP_400_BAD_REQUEST)
    username = data.get("telegram_username") or None
    last_name = data.get("last_name") or None
    identity = TelegramIdentity(
        telegram_user_id=telegram_user_id,
        telegram_username=str(username).strip() if username else None,
        first_name=first_name,
        last_name=str(last_name).strip() if last_name else None,
    )
    user = upsert_telegram_user(identity)
    return Response(UserSerializer(user).data)


@api_view(["GET"])
@authentication_classes([])
@permission_classes([IsBotService])
def bot_user_summary(_request: Request, telegram_user_id: int) -> Response:
    user = User.objects.filter(telegram_user_id=telegram_user_id, is_active=True).first()
    if user is None:
        return Response({"request_count": 0, "active_request_count": 0, "match_count": 0})
    requests = ItemRequest.objects.filter(user=user)
    match_count = (
        Match.objects.filter(
            Q(demand_request__user=user) | Q(supply_request__user=user),
            status__in=VISIBLE_MATCH_STATUSES,
        )
        .distinct()
        .count()
    )
    return Response(
        {
            "request_count": requests.count(),
            "active_request_count": requests.filter(status=RequestStatus.ACTIVE).count(),
            "match_count": match_count,
        }
    )


@api_view(["POST"])
@authentication_classes([])
@permission_classes([IsBotService])
def bot_create_request(request: Request) -> Response:
    data = dict(request.data) if isinstance(request.data, dict) else {}
    try:
        telegram_user_id = int(data.pop("telegram_user_id"))
    except (KeyError, TypeError, ValueError):
        return Response({"detail": "telegram_user_id is required"}, status=status.HTTP_400_BAD_REQUEST)
    user = User.objects.filter(telegram_user_id=telegram_user_id, is_active=True).first()
    if user is None:
        return Response({"detail": "Unknown Telegram user"}, status=status.HTTP_404_NOT_FOUND)
    try:
        item_request = create_item_request(user, data)
    except DjangoValidationError as exc:
        raise_api_validation(exc)
    return Response(ItemRequestSerializer(item_request).data, status=status.HTTP_201_CREATED)
