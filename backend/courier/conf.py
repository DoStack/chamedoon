"""Courier switches: the admin row (Courier settings) or the server environment turns each one on.

All of them default to off, so nothing new messages anyone until it is switched on on purpose.
"""

from __future__ import annotations

from django.conf import settings

from courier.models import CourierSettings

MAX_ALERTS_PER_USER = 5
INTRO_EXPIRE_HOURS = 72
# One person cannot be asked by more than this many people a day.
INTRO_TARGET_DAILY_LIMIT = 10


def row() -> CourierSettings:
    return CourierSettings.load()


def _env(name: str) -> bool:
    return bool(getattr(settings, name, False))


def bot_chat_enabled() -> bool:
    """Replies through the production webhook. The polling bot in bot/ is run on purpose and always answers."""
    return _env("COURIER_BOT_CHAT_ENABLED") or row().bot_chat_enabled


def intros_enabled() -> bool:
    return _env("COURIER_INTROS_ENABLED") or row().intros_enabled


def alerts_enabled() -> bool:
    return _env("COURIER_ALERTS_ENABLED") or row().alerts_enabled


def intro_daily_limit() -> int:
    return max(1, min(20, row().intro_daily_limit or 5))


def alert_daily_cap() -> int:
    return max(1, min(50, row().alert_daily_cap or 10))


def bot_username() -> str:
    return (getattr(settings, "TELEGRAM_BOT_USERNAME", "") or "").strip().lstrip("@")


def mini_app_link(start: str = "courier") -> str:
    """The courier Mini App page inside the courier bot (never the housing bot)."""
    username = bot_username()
    short_name = getattr(settings, "TELEGRAM_MINI_APP_SHORT_NAME", "app") or "app"
    if not username:
        return ""
    return f"https://t.me/{username}/{short_name}?startapp={start}"
