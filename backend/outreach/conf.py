"""Effective outreach configuration: the admin settings row first, then the server environment.

Same idea as home/conf.py. Every blank admin value falls back to the OUTREACH_* setting, so an
empty row behaves exactly like the environment did before the row existed.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.conf import settings

from outreach.models import OutreachAccount, OutreachSettings


def row() -> OutreachSettings:
    return OutreachSettings.load()


def _pick(value, env_name: str, default):
    return value if value is not None else getattr(settings, env_name, default)


def building_enabled() -> bool:
    return bool(_pick(row().enabled, "OUTREACH_ENABLED", False))


def sending_enabled() -> bool:
    return bool(_pick(row().sending_enabled, "OUTREACH_SENDING_ENABLED", False))


def daily_limit() -> int:
    return int(_pick(row().daily_limit, "OUTREACH_DAILY_LIMIT", 10))


def min_score() -> Decimal:
    return Decimal(str(_pick(row().min_score, "OUTREACH_MIN_SCORE", 80)))


def max_post_age() -> timedelta:
    return timedelta(days=int(_pick(row().max_post_age_days, "OUTREACH_MAX_POST_AGE_DAYS", 14)))


def cooldown() -> timedelta:
    return timedelta(days=int(_pick(row().cooldown_days, "OUTREACH_COOLDOWN_DAYS", 14)))


def send_window() -> tuple[int, int]:
    current = row()
    start = int(_pick(current.send_start_hour, "OUTREACH_SEND_START_HOUR", 10))
    end = int(_pick(current.send_end_hour, "OUTREACH_SEND_END_HOUR", 21))
    return max(0, min(23, start)), max(1, min(24, end))


def account_daily_limit(account: OutreachAccount) -> int:
    """0 means no per-account limit (only the total daily limit applies)."""
    if account.daily_limit is not None:
        return int(account.daily_limit)
    return int(_pick(row().account_daily_limit, "OUTREACH_ACCOUNT_DAILY_LIMIT", 0) or 0)


def account_gap(account: OutreachAccount) -> timedelta:
    if account.min_gap_minutes is not None:
        minutes = account.min_gap_minutes
    else:
        minutes = _pick(row().min_gap_minutes, "OUTREACH_MIN_GAP_MINUTES", 0) or 0
    return timedelta(minutes=int(minutes))


def effective() -> dict:
    """What each control resolves to right now, and whether it comes from the admin row or the environment."""
    current = row()

    def origin(value) -> str:
        return "admin" if value is not None else "env"

    start, end = send_window()
    return {
        "enabled": (building_enabled(), origin(current.enabled)),
        "sending_enabled": (sending_enabled(), origin(current.sending_enabled)),
        "daily_limit": (daily_limit(), origin(current.daily_limit)),
        "account_daily_limit": (
            int(_pick(current.account_daily_limit, "OUTREACH_ACCOUNT_DAILY_LIMIT", 0) or 0),
            origin(current.account_daily_limit),
        ),
        "min_gap_minutes": (
            int(_pick(current.min_gap_minutes, "OUTREACH_MIN_GAP_MINUTES", 0) or 0),
            origin(current.min_gap_minutes),
        ),
        "send_window_tehran": (f"{start}:00-{end}:00", origin(current.send_start_hour if current.send_start_hour is not None else current.send_end_hour)),
        "min_score": (int(min_score()), origin(current.min_score)),
        "max_post_age_days": (max_post_age().days, origin(current.max_post_age_days)),
        "cooldown_days": (cooldown().days, origin(current.cooldown_days)),
    }
