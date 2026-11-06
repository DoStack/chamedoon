"""Effective outreach configuration: the admin settings row first, then the server environment.

Same idea as home/conf.py. Every blank admin value falls back to the OUTREACH_* setting, so an
empty row behaves exactly like the environment.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.conf import settings

from outreach.models import OutreachAccount, OutreachKind, OutreachSettings

# Wave 1 and 2. P6 and the courier models exist but are switched on by name.
DEFAULT_KINDS = "S1,S1B,S2,P1,P1Q"


def row() -> OutreachSettings:
    return OutreachSettings.load()


def _pick(value, env_name: str, default):
    return value if value is not None else getattr(settings, env_name, default)


def building_enabled() -> bool:
    return bool(_pick(row().enabled, "OUTREACH_ENABLED", False))


def sending_enabled() -> bool:
    return bool(_pick(row().sending_enabled, "OUTREACH_SENDING_ENABLED", False))


def daily_limit() -> int:
    current = row()
    if current.daily_limit_from_accounts:
        return sum(account_daily_limit(account) for account in working_accounts())
    return int(_pick(current.daily_limit, "OUTREACH_DAILY_LIMIT", 5))


WORKING_HEARTBEAT = 30  # minutes


def working_accounts() -> list[OutreachAccount]:
    """Switched on, logged in, and its worker heard from in the last half hour."""
    from datetime import timedelta as _delta

    from django.utils import timezone

    since = timezone.now() - _delta(minutes=WORKING_HEARTBEAT)
    return list(OutreachAccount.objects.filter(enabled=True, session_ok=True, last_heartbeat_at__gte=since))


def min_score() -> Decimal:
    return Decimal(str(_pick(row().min_score, "OUTREACH_MIN_SCORE", 80)))


def max_post_age() -> timedelta:
    return timedelta(days=int(_pick(row().max_post_age_days, "OUTREACH_MAX_POST_AGE_DAYS", 14)))


def cooldown() -> timedelta:
    return timedelta(days=int(_pick(row().cooldown_days, "OUTREACH_COOLDOWN_DAYS", 14)))


def send_window() -> tuple[int, int]:
    """Hours on the recipient city's clock."""
    current = row()
    start = int(_pick(current.send_start_hour, "OUTREACH_SEND_START_HOUR", 10))
    end = int(_pick(current.send_end_hour, "OUTREACH_SEND_END_HOUR", 20))
    return max(0, min(23, start)), max(1, min(24, end))


def enabled_kinds() -> set[str]:
    raw = row().enabled_kinds.strip() or getattr(settings, "OUTREACH_ENABLED_KINDS", DEFAULT_KINDS)
    wanted = {part.strip().upper() for part in raw.split(",") if part.strip()}
    return wanted & set(OutreachKind.values)


def auto_queue() -> bool:
    return bool(row().auto_queue)


def agent_enabled() -> bool:
    return bool(row().agent_enabled)


def operator_ids() -> set[int]:
    return {int(part) for part in row().operator_ids.replace(",", " ").split() if part.strip().isdigit()}


def account_daily_limit(account: OutreachAccount) -> int:
    if account.daily_limit is not None:
        return int(account.daily_limit)
    return int(_pick(row().account_daily_limit, "OUTREACH_ACCOUNT_DAILY_LIMIT", 30) or 0)


def account_hourly_limit(account: OutreachAccount) -> int:
    if account.hourly_limit is not None:
        return int(account.hourly_limit)
    return int(_pick(row().account_hourly_limit, "OUTREACH_ACCOUNT_HOURLY_LIMIT", 3) or 0)


def account_gap(account: OutreachAccount) -> timedelta:
    if account.min_gap_minutes is not None:
        minutes = account.min_gap_minutes
    else:
        minutes = _pick(row().min_gap_minutes, "OUTREACH_MIN_GAP_MINUTES", 15) or 0
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
            int(_pick(current.account_daily_limit, "OUTREACH_ACCOUNT_DAILY_LIMIT", 30) or 0),
            origin(current.account_daily_limit),
        ),
        "account_hourly_limit": (
            int(_pick(current.account_hourly_limit, "OUTREACH_ACCOUNT_HOURLY_LIMIT", 3) or 0),
            origin(current.account_hourly_limit),
        ),
        "min_gap_minutes": (
            int(_pick(current.min_gap_minutes, "OUTREACH_MIN_GAP_MINUTES", 15) or 0),
            origin(current.min_gap_minutes),
        ),
        "send_window_local": (f"{start}:00-{end}:00", origin(current.send_start_hour if current.send_start_hour is not None else current.send_end_hour)),
        "enabled_kinds": (",".join(sorted(enabled_kinds())), "admin" if current.enabled_kinds.strip() else "env"),
        "min_score": (int(min_score()), origin(current.min_score)),
        "max_post_age_days": (max_post_age().days, origin(current.max_post_age_days)),
        "cooldown_days": (cooldown().days, origin(current.cooldown_days)),
    }
