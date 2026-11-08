"""The sending accounts: each has its own limits and its own pause, and a limited account hands over to the next.

Sending goes through `claim_for_account` only. Telegram's limits are per account, so a flood pauses only that account
(Mohammad, 2026-11-06). Two PEER_FLOODs of one account in 7 days switch it off. Everything stops only when no account
can send any more; only a person clears that stop.
"""

from __future__ import annotations

import random
import re
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from outreach import conf
from outreach.models import OutreachAccount, OutreachAccountEvent, OutreachMessage
from outreach.notify import tell_mohammad
from outreach.services import ATTEMPTED_STATUSES, PEER_FLOOD, PEER_FLOOD_PAUSE, claim_next_message

ACCOUNT_NAME = re.compile(r"^[a-z0-9_-]{1,32}$")
# One first message every 15 to 25 minutes per account, at random (Mohammad, 2026-11-06, after two floods).
# An account with its own gap waits a random time between that gap and twice it.
GAP_JITTER_MINUTES = (15, 25)
SESSION_INVALID = "SESSION_INVALID"
FLOOD_WAIT = "FLOOD_WAIT"
FLOOD_WINDOW = timedelta(days=7)


def clean_account_name(value) -> str:
    return str(value or "").strip().lstrip("@").lower()


def attempted_since(account: OutreachAccount, since: datetime) -> int:
    """Texts and intro videos alike: each is one message to Telegram."""
    texts = OutreachMessage.objects.filter(account=account, status__in=ATTEMPTED_STATUSES, claimed_at__gte=since).count()
    videos = OutreachMessage.objects.filter(account=account, video_sent_at__gte=since).count()
    return texts + videos


def attempted_today_for(account: OutreachAccount, now: datetime) -> int:
    return attempted_since(account, now - timedelta(hours=24))


def next_pause(account: OutreachAccount) -> timedelta:
    gap = conf.account_gap(account)
    if gap:
        return gap * random.uniform(1, 2)
    return timedelta(minutes=random.uniform(*GAP_JITTER_MINUTES))


def account_gate(account: OutreachAccount, now: datetime, *, need: int = 1, video: bool = False) -> str:
    """Empty string when this account may send `need` messages now, else why not. A video skips the pace."""
    if not account.enabled:
        return "account_disabled"
    if not account.session_ok:
        return "account_session"
    if account.paused_until and account.paused_until > now:
        return "account_paused"
    daily = conf.account_daily_limit(account, now)
    if daily and attempted_since(account, now - timedelta(hours=24)) + need > daily:
        return "account_daily_limit"
    if video:
        return ""
    hourly = conf.account_hourly_limit(account)
    if hourly and attempted_since(account, now - timedelta(hours=1)) + need > hourly:
        return "account_hourly_limit"
    gap = conf.account_gap(account)
    if gap and account.last_sent_at and account.last_sent_at > now - gap:
        return "account_gap"
    if account.next_send_at and account.next_send_at > now:
        return "account_gap"
    return ""


def claim_for_account(name, now: datetime | None = None) -> tuple[OutreachMessage | None, str]:
    """Claim the next message for `name`. An unknown name is registered (switched on) so it shows in the admin."""
    now = now or timezone.now()
    name = clean_account_name(name)
    if not ACCOUNT_NAME.fullmatch(name):
        return None, "bad_account"
    with transaction.atomic():
        OutreachAccount.objects.get_or_create(name=name)
        account = OutreachAccount.objects.select_for_update().get(name=name)
        from outreach.videos import claim_video

        gate = account_gate(account, now, video=True)
        if gate:
            return None, gate
        video = claim_video(account, now)
        if video is not None:
            # A video answers someone who wrote to us: it keeps the account's daily count but not its pace.
            return video, ""
        gate = account_gate(account, now)
        if gate:
            return None, gate

        message, reason = claim_next_message(now, account=account, other_ready=lambda other: not account_gate(other, now))
        if message is None:
            return None, reason
        message.account = account
        message.save(update_fields=["account", "updated_at"])
        account.last_sent_at = now
        account.next_send_at = now + next_pause(account)
        account.save(update_fields=["last_sent_at", "next_send_at"])
        return message, ""


def _log(account: OutreachAccount, code: str, detail: str = "") -> None:
    OutreachAccountEvent.objects.create(account=account, code=code[:64], detail=detail[:300])


def _pause(account: OutreachAccount, until: datetime, reason: str) -> None:
    if not account.paused_until or account.paused_until < until:
        account.paused_until = until
        account.pause_reason = reason[:64]
        account.save(update_fields=["paused_until", "pause_reason"])


def record_peer_flood(account: OutreachAccount, now: datetime) -> None:
    _log(account, PEER_FLOOD)
    _pause(account, now + PEER_FLOOD_PAUSE, PEER_FLOOD)
    floods = OutreachAccountEvent.objects.filter(account=account, code=PEER_FLOOD, created_at__gte=now - FLOOD_WINDOW)
    if floods.count() >= 2:
        account.enabled = False
        account.save(update_fields=["enabled"])
        tell_mohammad(f"⚠️ اکانت {account.name} در ۷ روز دو بار PEER_FLOOD گرفت و خاموش شد.")
    else:
        tell_mohammad(f"⚠️ اکانت {account.name} محدود شد (PEER_FLOOD) و ۴۸ ساعت صبر می‌کند. پیام‌ها با اکانت‌های دیگر می‌روند.")
    _stop_if_none_can_send(now)


def record_flood_wait(account: OutreachAccount, seconds: int, now: datetime) -> None:
    _log(account, FLOOD_WAIT, str(seconds))
    _pause(account, now + timedelta(seconds=int(seconds)), FLOOD_WAIT)
    _stop_if_none_can_send(now)


def _stop_if_none_can_send(now: datetime) -> None:
    """Every switched-on, logged-in account is paused: an FYI only. No global stop, because each pause ends by itself and a
    global stop does not, so sending stayed off for two days after the pauses ended (2026-11-06 to 11-08)."""
    usable = OutreachAccount.objects.filter(enabled=True, session_ok=True)
    if not usable.exclude(paused_until__gt=now).exists():
        tell_mohammad("FYI outreach: همه‌ی اکانت‌ها فعلاً محدودند؛ هر کدوم بعد از پایان محدودیتش خودش دوباره می‌فرسته.")


def record_session_invalid(account: OutreachAccount) -> None:
    if not account.session_ok:
        return
    _log(account, SESSION_INVALID)
    account.session_ok = False
    account.save(update_fields=["session_ok"])
    tell_mohammad(f"⚠️ ورود اکانت {account.name} باطل شد و این اکانت متوقف است. کد ورود را فقط خودت می‌زنی.")


def record_account_heartbeat(name, now: datetime | None = None) -> None:
    name = clean_account_name(name)
    if ACCOUNT_NAME.fullmatch(name):
        OutreachAccount.objects.filter(name=name).update(last_heartbeat_at=now or timezone.now())
