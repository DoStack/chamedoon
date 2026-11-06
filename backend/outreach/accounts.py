"""The sending accounts: each has its own limits and its own pause, and a limited account hands over to the next.

Sending goes through `claim_for_account` only. Telegram's limits are per account, so nothing here pauses the others,
except the stop rules: two PEER_FLOODs of one account in 7 days switch it off, two accounts limited in a week, or
every account limited at once, stop everything. Only a person clears a stop.
"""

from __future__ import annotations

import random
import re
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from outreach import conf
from outreach.models import OutreachAccount, OutreachAccountEvent, OutreachMessage, OutreachState
from outreach.notify import tell_mohammad
from outreach.services import ATTEMPTED_STATUSES, PEER_FLOOD, PEER_FLOOD_PAUSE, claim_next_message

ACCOUNT_NAME = re.compile(r"^[a-z0-9_-]{1,32}$")
# A short random pause between two sends of one account, so messages never go out in one burst (Mohammad, 2026-11-06).
GAP_JITTER_MINUTES = (1, 3)
SESSION_INVALID = "SESSION_INVALID"
FLOOD_WAIT = "FLOOD_WAIT"
FLOOD_WINDOW = timedelta(days=7)
# Rollout rule (Mohammad, 2026-11-05): any PEER_FLOOD or FLOOD_WAIT stops all sending until a person clears it.
STOP_ON_ANY_FLOOD = True


def clean_account_name(value) -> str:
    return str(value or "").strip().lstrip("@").lower()


def attempted_since(account: OutreachAccount, since: datetime) -> int:
    """Texts and intro videos alike: each is one message to Telegram."""
    texts = OutreachMessage.objects.filter(account=account, status__in=ATTEMPTED_STATUSES, claimed_at__gte=since).count()
    videos = OutreachMessage.objects.filter(account=account, video_sent_at__gte=since).count()
    return texts + videos


def attempted_today_for(account: OutreachAccount, now: datetime) -> int:
    return attempted_since(account, now - timedelta(hours=24))


def account_gate(account: OutreachAccount, now: datetime, *, need: int = 1) -> str:
    """Empty string when this account may send `need` messages now, else why not."""
    if not account.enabled:
        return "account_disabled"
    if not account.session_ok:
        return "account_session"
    if account.paused_until and account.paused_until > now:
        return "account_paused"
    daily = conf.account_daily_limit(account)
    if daily and attempted_since(account, now - timedelta(hours=24)) + need > daily:
        return "account_daily_limit"
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
        gate = account_gate(account, now)
        if gate:
            return None, gate
        from outreach.videos import claim_video

        video = claim_video(account, now)
        if video is not None:
            account.last_sent_at = now
            account.next_send_at = now + conf.account_gap(account) + timedelta(minutes=random.uniform(*GAP_JITTER_MINUTES))
            account.save(update_fields=["last_sent_at", "next_send_at"])
            return video, ""
        # A first message with its video is two messages: both must fit.
        room = not account_gate(account, now, need=2)
        message, reason = claim_next_message(
            now, account=account, other_ready=lambda other: not account_gate(other, now), room_for_video=room
        )
        if message is None:
            return None, reason
        message.account = account
        message.save(update_fields=["account", "updated_at"])
        account.last_sent_at = now
        account.next_send_at = now + conf.account_gap(account) + timedelta(minutes=random.uniform(*GAP_JITTER_MINUTES))
        account.save(update_fields=["last_sent_at", "next_send_at"])
        return message, ""


def _log(account: OutreachAccount, code: str, detail: str = "") -> None:
    OutreachAccountEvent.objects.create(account=account, code=code[:64], detail=detail[:300])


def _pause(account: OutreachAccount, until: datetime, reason: str) -> None:
    if not account.paused_until or account.paused_until < until:
        account.paused_until = until
        account.pause_reason = reason[:64]
        account.save(update_fields=["paused_until", "pause_reason"])


def _stop_all(reason: str) -> None:
    state = OutreachState.load()
    if state.stopped:
        return
    state.stopped = True
    state.pause_reason = reason[:64]
    state.save(update_fields=["stopped", "pause_reason", "updated_at"])
    tell_mohammad(f"⛔️ ارسال outreach کامل متوقف شد: {reason}. فقط خودت از پنل ادمین (Outreach state) برش می‌داری.")


def record_peer_flood(account: OutreachAccount, now: datetime) -> None:
    _log(account, PEER_FLOOD)
    _pause(account, now + PEER_FLOOD_PAUSE, PEER_FLOOD)
    since = now - FLOOD_WINDOW
    floods = OutreachAccountEvent.objects.filter(code=PEER_FLOOD, created_at__gte=since)
    if floods.filter(account=account).count() >= 2:
        account.enabled = False
        account.save(update_fields=["enabled"])
        tell_mohammad(f"⚠️ اکانت {account.name} در ۷ روز دو بار PEER_FLOOD گرفت و خاموش شد.")
    else:
        tell_mohammad(f"⚠️ اکانت {account.name} محدود شد (PEER_FLOOD) و ۴۸ ساعت صبر می‌کند. پیام‌ها با اکانت بعدی می‌روند.")
    if STOP_ON_ANY_FLOOD:
        _stop_all(f"{account.name} PEER_FLOOD گرفت (قانون دوره‌ی شروع)")
        return
    if floods.values("account").distinct().count() >= 2:
        _stop_all("دو اکانت در یک هفته PEER_FLOOD گرفتند")
        return
    usable = OutreachAccount.objects.filter(enabled=True, session_ok=True)
    if usable.count() and not usable.exclude(paused_until__gt=now).exists():
        _stop_all("همه‌ی اکانت‌ها محدودند")


def record_flood_wait(account: OutreachAccount, seconds: int, now: datetime) -> None:
    _log(account, FLOOD_WAIT, str(seconds))
    _pause(account, now + timedelta(seconds=int(seconds)), FLOOD_WAIT)
    if STOP_ON_ANY_FLOOD:
        _stop_all(f"{account.name} FLOOD_WAIT {int(seconds)}s گرفت (قانون دوره‌ی شروع)")


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
