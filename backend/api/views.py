from django.conf import settings
from django.db import connection
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response

from api.permissions import IsTelegramUser
from api.serializers import TelegramAuthSerializer, UserSerializer
from users.exceptions import TelegramAuthError
from users.services import upsert_telegram_user
from users.telegram import parse_and_validate_init_data, parse_dev_user
from users.tokens import issue_access_token


def _safe_db_error(exc: BaseException) -> str:
    import re

    text = f"{type(exc).__name__}: {exc}"
    text = re.sub(r":[^:@/\s]+@", ":***@", text)
    return text[:400]


@api_view(["GET"])
@permission_classes([AllowAny])
def health(_request: Request) -> Response:
    database_ok = False
    database_error = ""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        database_ok = True
    except Exception as exc:
        database_ok = False
        database_error = _safe_db_error(exc)

    payload = {
        "status": "ok" if database_ok else "degraded",
        "service": "koolbar-backend",
        "database": "ok" if database_ok else "error",
        "database_source": getattr(settings, "DATABASE_SOURCE", ""),
    }
    if database_error:
        payload["database_error"] = database_error
    http_status = status.HTTP_200_OK if database_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return Response(payload, status=http_status)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def telegram_auth(request: Request) -> Response:
    serializer = TelegramAuthSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    init_data = (serializer.validated_data.get("init_data") or "").strip()
    dev_user = serializer.validated_data.get("dev_user")

    try:
        if init_data:
            identity = parse_and_validate_init_data(init_data)
        elif dev_user:
            identity = parse_dev_user(dev_user)
        else:
            raise TelegramAuthError("init_data is required", status_code=400)
        user = upsert_telegram_user(identity)
    except TelegramAuthError as exc:
        return Response({"detail": exc.message}, status=exc.status_code)

    if not user.is_active:
        return Response({"detail": "User is deactivated"}, status=status.HTTP_403_FORBIDDEN)

    return Response(
        {
            "token": issue_access_token(user),
            "user": UserSerializer(user).data,
        }
    )


@api_view(["GET"])
@permission_classes([IsTelegramUser])
def me(request: Request) -> Response:
    return Response(UserSerializer(request.user).data)
