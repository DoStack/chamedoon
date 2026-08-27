from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from django.conf import settings

from users.exceptions import TelegramAuthError
from users.models import User


def issue_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    hours = int(getattr(settings, "JWT_ACCESS_TOKEN_HOURS", 24 * 30))
    payload = {
        "user_id": user.id,
        "telegram_user_id": user.telegram_user_id,
        "iat": now,
        "exp": now + timedelta(hours=hours),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise TelegramAuthError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TelegramAuthError("Invalid token") from exc

    if "user_id" not in payload or "telegram_user_id" not in payload:
        raise TelegramAuthError("Invalid token")
    return payload
