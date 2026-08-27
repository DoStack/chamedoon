from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

from django.conf import settings

from users.exceptions import TelegramAuthError

TELEGRAM_MINI_APP_KEY = b"WebAppData"


@dataclass(frozen=True)
class TelegramIdentity:
    telegram_user_id: int
    telegram_username: str | None
    first_name: str
    last_name: str | None


def parse_and_validate_init_data(init_data: str) -> TelegramIdentity:
    if not init_data or not init_data.strip():
        raise TelegramAuthError("init_data is required", status_code=400)

    bot_token = settings.TELEGRAM_BOT_TOKEN
    if not bot_token:
        raise TelegramAuthError("Telegram bot token is not configured", status_code=503)

    fields = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    received_hash = fields.pop("hash", "")
    if not received_hash:
        raise TelegramAuthError("init_data is missing hash", status_code=400)

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret_key = hmac.new(TELEGRAM_MINI_APP_KEY, bot_token.encode("utf-8"), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise TelegramAuthError("Invalid Telegram authentication data")

    _assert_auth_date_fresh(fields.get("auth_date"))
    return _identity_from_user_json(fields.get("user", ""))


def parse_dev_user(payload: dict) -> TelegramIdentity:
    if not settings.DEBUG:
        raise TelegramAuthError("Dev authentication is disabled")

    try:
        telegram_user_id = int(payload["telegram_user_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TelegramAuthError("dev_user.telegram_user_id is required", status_code=400) from exc

    first_name = str(payload.get("first_name") or "").strip()
    if not first_name:
        raise TelegramAuthError("dev_user.first_name is required", status_code=400)

    username = payload.get("telegram_username") or None
    last_name = payload.get("last_name") or None
    return TelegramIdentity(
        telegram_user_id=telegram_user_id,
        telegram_username=str(username).strip() if username else None,
        first_name=first_name,
        last_name=str(last_name).strip() if last_name else None,
    )


def _assert_auth_date_fresh(auth_date_raw: str | None) -> None:
    try:
        auth_date = int(auth_date_raw or "")
    except ValueError as exc:
        raise TelegramAuthError("init_data has an invalid auth_date", status_code=400) from exc

    max_age = int(getattr(settings, "TELEGRAM_AUTH_MAX_AGE_SECONDS", 86400))
    if auth_date <= 0 or abs(int(time.time()) - auth_date) > max_age:
        raise TelegramAuthError("Telegram authentication data has expired")


def _identity_from_user_json(user_raw: str) -> TelegramIdentity:
    try:
        user_data = json.loads(user_raw)
    except json.JSONDecodeError as exc:
        raise TelegramAuthError("init_data has an invalid user payload", status_code=400) from exc

    try:
        telegram_user_id = int(user_data["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TelegramAuthError("Telegram user id is missing", status_code=400) from exc

    first_name = str(user_data.get("first_name") or "").strip()
    if not first_name:
        raise TelegramAuthError("Telegram first_name is required", status_code=400)

    username = user_data.get("username")
    last_name = user_data.get("last_name")
    return TelegramIdentity(
        telegram_user_id=telegram_user_id,
        telegram_username=str(username).strip() if username else None,
        first_name=first_name,
        last_name=str(last_name).strip() if last_name else None,
    )
