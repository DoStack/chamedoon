from __future__ import annotations

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request

from users.exceptions import TelegramAuthError
from users.models import User
from users.tokens import decode_access_token


class TelegramJWTAuthentication(BaseAuthentication):
    def authenticate(self, request: Request) -> tuple[User, str] | None:
        header = request.headers.get("Authorization", "")
        if not header:
            return None
        if not header.startswith("Bearer "):
            raise AuthenticationFailed("Invalid authorization header")

        token = header.removeprefix("Bearer ").strip()
        if not token:
            raise AuthenticationFailed("Invalid authorization header")

        try:
            payload = decode_access_token(token)
        except TelegramAuthError as exc:
            raise AuthenticationFailed(exc.message) from exc

        user = User.objects.filter(
            id=payload["user_id"],
            telegram_user_id=payload["telegram_user_id"],
            is_active=True,
        ).first()
        if user is None:
            raise AuthenticationFailed("Invalid token")
        return (user, token)

    def authenticate_header(self, request: Request) -> str:
        return "Bearer"
