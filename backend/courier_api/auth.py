"""The same token as the rest of the API, with one code for each way it can fail."""

from __future__ import annotations

import jwt
from django.conf import settings
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.request import Request

from courier_api.errors import ApiError
from users.models import User


class CourierJWTAuthentication(BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request: Request):
        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != self.keyword.lower().encode():
            return None
        if len(parts) != 2:
            raise ApiError("auth_invalid")
        token = parts[1].decode(errors="replace")
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        except jwt.ExpiredSignatureError as exc:
            raise ApiError("token_expired") from exc
        except jwt.InvalidTokenError as exc:
            raise ApiError("auth_invalid") from exc
        user = User.objects.filter(id=payload.get("user_id"), telegram_user_id=payload.get("telegram_user_id")).first()
        if user is None:
            raise ApiError("auth_invalid")
        if not user.is_active:
            raise ApiError("user_blocked")
        return (user, token)

    def authenticate_header(self, request: Request) -> str:
        return self.keyword
