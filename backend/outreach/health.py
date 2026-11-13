"""Truth and health layer (outreach operating plan v1, 2026-11-08).

Every 5 minutes (`manage.py outreach_health`, VPS crontab) the DB is read and every incident is told to Mohammad with
the raw facts and the fix applied. At 20:30 Toronto the daily report goes. Nothing here guesses: every line comes from
rows in the DB. "Banned" is said only on a real Telegram ban error (`accounts.BAN_ERRORS`).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Q
from django.utils import timezone

from outreach import cities, conf
from outreach.accounts import BAN_ERRORS, STATE, account_gate, unreplied_since
from outreach.models import (
    OutreachAccount,
    OutreachAccountEvent,
    OutreachHealthEvent,
    OutreachMessage,
    OutreachState,
    OutreachStatus,
    OutreachTurn,
    TurnDirection,
    TurnStatus,
)
from outreach.notify import tell_mohammad

TORONTO = ZoneInfo("America/Toronto")
HEARTBEAT_STALE = timedelta(minutes=15)
IDLE_INCIDENT = timedelta(minutes=45)
# Sends must have been allowed for 3 cron runs (15 minutes) before "idle" is said: a cap slot that just opened is not idle.
IDLE_GRACE = timedelta(minutes=15)
REPEAT_AFTER = timedelta(minutes=60)
LONG_PAUSE = timedelta(hours=2)
UNANSWERED_AFTER = timedelta(minutes=10)
# A reply the guard held (action_detail.blocked) and nothing else went: told after 5 minutes (audit 2026-11-11 20:15,
# conv 27 T230 sat 7+ minutes on a dedupe block with no alert).
BLOCKED_AFTER = timedelta(minutes=5)
BLOCKED_LOOKBACK = timedelta(hours=24)
REPEATED_FREE_FLOODS = 3
REPORT_AT = (20, 30)
WORKER_ERRORS_ALERT = 5


@dataclass
class Incident:
    key: str
    facts: str
    fix: str = "هیچ"
    restart: bool = False
    # What makes this incident new: an unchanged state is told once, never again (Mohammad, 2026-11-10).
    state: str = ""


@dataclass
class Result:
    incidents: list[Incident] = field(default_factory=list)
    told: list[str] = field(default_factory=list)
    restart: bool = False
    report: str = ""


def _when(value: datetime | None) -> str:
    return timezone.localtime(value, TORONTO).strftime("%m-%d %H:%M") + " Toronto" if value else "never"


def _sendable_now(account: OutreachAccount, now: datetime) -> int:
    """Queued first messages this account could send now: kind on, recipient inside 10:00 to 20:00."""
    from outreach.models import FIRST_CONTACT_KINDS, OutreachKind
    from outreach.services import message_place

    kinds = [kind for kind in (*FIRST_CONTACT_KINDS, OutreachKind.P1G) if kind in conf.enabled_kinds()]
    queued = OutreachMessage.objects.filter(status=OutreachStatus.QUEUED, kind__in=kinds).filter(Q(account__isnull=True) | Q(account=account))
    if account.fresh_hours:
        # The claim's own rule: this account writes first messages only to posts this fresh (monis: 24 hours).
        queued = queued.filter(Q(source_at__gte=now - timedelta(hours=account.fresh_hours)) | ~Q(kind__in=list(FIRST_CONTACT_KINDS)))
    from outreach.services import skip_reason

    # The same filters the claim uses (place window and skip reasons), so this count and the claim agree.
    return sum(1 for message in queued[:300] if cities.in_send_window(*message_place(message), now) and not skip_reason(message, now))


def last_state(account: OutreachAccount) -> tuple[str, datetime | None]:
    event = account.events.filter(code=STATE).first()
    return (event.detail, event.created_at) if event else ("", None)


def check(now: datetime | None = None, *, worker_errors: int = 0) -> list[Incident]:
    now = now or timezone.now()
    found: list[Incident] = []
    state = OutreachState.load()
    if state.stopped:
        found.append(Incident("global_stop", f"توقف کلی روشنه: «{state.pause_reason}». (از اپ خودکار گذاشته نمی‌شه؛ دستیه.)", state=state.pause_reason or "on"))
    if state.paused_until and state.paused_until > now:
        found.append(Incident("global_pause", f"مکث کلی تا {_when(state.paused_until)}: «{state.pause_reason}».", state=f"{state.paused_until:%Y-%m-%d %H:%M}"))
    for account in OutreachAccount.objects.order_by("name"):
        if not account.enabled:
            # Off is never a quiet state: say it, with the event that switched it off (2026-11-08: all three were off
            # after one recipient's PEER_FLOOD and the check said nothing).
            event = account.events.exclude(code__in=[STATE, "SPAMBOT_ASK", "SPAMBOT"]).first()
            cause = f"{event.code} {event.detail}".strip() + f" در {_when(event.created_at)}" if event else "دلیلی ثبت نشده"
            paused = f"{account.paused_until:%Y-%m-%d %H:%M}" if account.paused_until else ""
            ended = account.paused_until is not None and account.paused_until <= now
            # The limit ran out but the account stays off: nothing turns it back on except the admin action, so this is
            # told as its own state (audit 2026-11-12 02:22: niloofar and sara sat off silently after 02:09).
            tail = f" محدودیتش {_when(account.paused_until)} تموم شده ولی خودکار روشن نمی‌شه؛ روشن کردنش با admin و تصمیم محمده." if ended else ""
            found.append(
                Incident(
                    f"disabled_{account.name}",
                    f"{account.name}: خاموشه (enabled=False). آخرین رویداد: {cause}. آخرین ارسال {_when(account.last_sent_at)}.{tail}",
                    state=f"off|{event.code if event else ''}|{paused}|{'ended' if ended else 'on'}",
                )
            )
            continue
        if not account.session_ok:
            event = account.events.filter(code="SESSION_INVALID").first()
            error = event.detail if event else ""
            word = "بن شده (خطای تلگرام " + error + ")" if error in BAN_ERRORS else f"ورودش باطل شده ({error or 'session'})"
            found.append(Incident(f"session_{account.name}", f"{account.name}: {word}. آخرین ارسال {_when(account.last_sent_at)}.", state=f"session|{error}"))
            continue
        if account.last_heartbeat_at is None or account.last_heartbeat_at < now - HEARTBEAT_STALE:
            found.append(
                Incident(
                    f"heartbeat_{account.name}",
                    f"{account.name}: آخرین heartbeat {_when(account.last_heartbeat_at)}، آخرین ارسال {_when(account.last_sent_at)}.",
                    "ری‌استارت worker",
                    restart=True,
                )
            )
            continue
        repeats = account.events.filter(code="PEER_FLOOD_FREE", created_at__gte=now - timedelta(hours=24)).count()
        if repeats >= REPEATED_FREE_FLOODS:
            # Blocked again and again although SpamBot says free: Mohammad should know.
            found.append(Incident(f"repeat_flood_{account.name}", f"{account.name}: {repeats} بار PEER_FLOOD در ۲۴ ساعت اخیر با SpamBot «آزاد»؛ هر بار پیام‌های اول ۱ ساعت صبر کردن.", state="repeat"))
        if account.paused_until and account.paused_until > now:
            # A Telegram limit ends by itself; one longer than 2 hours is an incident, never a quiet reason.
            if account.paused_until - now > LONG_PAUSE:
                event = account.events.filter(code__in=["PEER_FLOOD", "FLOOD_WAIT"]).first()
                said = f" (ثبت‌شده: {event.code} {event.detail})" if event else ""
                found.append(
                    Incident(
                        f"paused_{account.name}",
                        f"{account.name}: محدودیت موقت {account.pause_reason} تا {_when(account.paused_until)}{said}. بن نیست.",
                        state=f"paused|{account.pause_reason}|{account.paused_until:%Y-%m-%d %H:%M}",
                    )
                )
            continue
        quiet_since = account.last_sent_at
        if quiet_since is not None and quiet_since > now - IDLE_INCIDENT:
            continue
        # Idle only when sending has been allowed for the whole grace: now and 15 minutes ago (cap, pause, gap).
        gate = account_gate(account, now) or account_gate(account, now - IDLE_GRACE)
        sendable = 0 if gate else _sendable_now(account, now)
        if gate or not sendable:
            # Not sending for a logged reason (budget, gap, nothing in window): not an incident.
            continue
        reason, since = last_state(account)
        found.append(
            Incident(
                f"idle_{account.name}",
                f"{account.name}: {sendable} پیام آماده‌ی ارسال تو پنجره، ولی از {_when(quiet_since)} چیزی نفرستاده. "
                f"آخرین وضعیت claim: «{reason or '-'}» از {_when(since)}. بی‌جواب ۲۴ ساعت: {unreplied_since(account, now - timedelta(hours=24))}.",
                # The heartbeat is fresh, so the loop runs: a restart would not help; the reason above says why.
                "هیچ (worker زنده است)",
            )
        )
    waiting = (
        OutreachTurn.objects.filter(direction=TurnDirection.IN, status=TurnStatus.PENDING, created_at__lt=now - UNANSWERED_AFTER)
        .exclude(conversation__state="stopped")
        .select_related("conversation", "conversation__account")
        .order_by("created_at")
    )
    for turn in waiting[:10]:
        # Someone wrote and nobody answered in 10 minutes: always an incident (Mohammad, 2026-11-09).
        minutes = int((now - turn.created_at).total_seconds() // 60)
        found.append(
            Incident(
                f"unanswered_{turn.pk}",
                f"#c{turn.conversation_id} @{turn.conversation.username} ({turn.conversation.account.name}): پیام {minutes} دقیقه‌ست بی‌جوابه «{(turn.text or '[عکس]')[:80]}».",
                "ری‌استارت worker",
                restart=True,
            )
        )
    found.extend(blocked_silent(now))
    from outreach.services import _expire_stale_claims

    # The lease runs here too: with every account gated no claim runs, and open claims never expired (audit
    # 2026-11-13 00:18: #372, #375, #392 past HUMAN_LEASE).
    _expire_stale_claims(now)
    found.extend(human_claims_limited(now))
    if worker_errors >= WORKER_ERRORS_ALERT:
        found.append(Incident("worker_errors", f"worker در ۵ دقیقه‌ی اخیر {worker_errors} خطای backend دیده."))
    return found


def human_claims_limited(now: datetime) -> list[Incident]:
    """A human claim still open on an account that is off or limited: told once per claim, nothing is changed (audit
    2026-11-12 18:15: #392 on monis and #375 on niloofar were handed out 1 s after each account's PEER_FLOOD)."""
    from outreach.models import SentBy

    found: list[Incident] = []
    rows = OutreachMessage.objects.filter(status=OutreachStatus.SENDING, sent_by=SentBy.HUMAN).select_related("account")
    for message in rows:
        account = message.account
        if account is None:
            continue
        why = "خاموشه" if not account.enabled else (f"تا {_when(account.paused_until)} محدوده ({account.pause_reason})" if account.paused_until and account.paused_until > now else "")
        if not why:
            continue
        found.append(
            Incident(
                f"human_claim_limited_{message.pk}",
                f"#{message.pk} @{message.recipient_username}: ارسال دستی روی {account.name} بازه ولی حساب {why}؛ ارسال از این حساب احتمالاً PEER_FLOOD می‌گیره.",
                "هیچ؛ تصمیم با محمد",
                state=why,
            )
        )
    return found


def blocked_silent(now: datetime) -> list[Incident]:
    """Inbound turns whose reply the guard blocked, 5+ minutes ago, with no message of ours after them."""
    held = (
        OutreachTurn.objects.filter(
            direction=TurnDirection.IN,
            action_detail__has_key="blocked",
            created_at__lt=now - BLOCKED_AFTER,
            created_at__gte=now - BLOCKED_LOOKBACK,
        )
        .exclude(conversation__state="stopped")
        .select_related("conversation", "conversation__account")
        .order_by("created_at")
    )
    found: list[Incident] = []
    for turn in held[:20]:
        if OutreachTurn.objects.filter(conversation_id=turn.conversation_id, direction=TurnDirection.OUT, created_at__gt=turn.created_at).exists():
            continue
        if OutreachTurn.objects.filter(conversation_id=turn.conversation_id, direction=TurnDirection.IN, created_at__gt=turn.created_at).exclude(action_detail__has_key="blocked").exists():
            # A later message of theirs is answered or pending: that one carries the chat.
            continue
        minutes = int((now - turn.created_at).total_seconds() // 60)
        reason = (turn.action_detail or {}).get("blocked")
        found.append(
            Incident(
                f"blocked_{turn.pk}",
                f"#c{turn.conversation_id} @{turn.conversation.username} ({turn.conversation.account.name}): جواب {minutes} دقیقه‌ست "
                f"بلاک شده ({reason}) و چیزی نرفته «{(turn.text or '[عکس]')[:80]}».",
                "هیچ؛ جواب دستی یا بازنویسی لازمه",
            )
        )
    return found


RUN_KEY = "health_run"
CHECK_STALE = timedelta(minutes=15)


def self_check(now: datetime | None = None) -> bool:
    """Called on every worker heartbeat: when the 5-minute check has not run for 15 minutes, tell Mohammad (once an
    hour). A dead health check never stays silent (2026-11-09: cron ran a broken health.sh for 9 hours)."""
    now = now or timezone.now()
    last = OutreachHealthEvent.objects.filter(key=RUN_KEY).values_list("created_at", flat=True).first()
    if last is not None and last >= now - CHECK_STALE:
        return True
    if OutreachHealthEvent.objects.filter(key="health_dead", created_at__gte=now - REPEAT_AFTER).exists():
        return False
    event = OutreachHealthEvent.objects.create(key="health_dead", detail=f"last run {last}")
    OutreachHealthEvent.objects.filter(pk=event.pk).update(created_at=now)
    tell_mohammad(f"⚠️ وضعیت outreach: health check از {_when(last)} اجرا نشده (بیش از ۱۵ دقیقه). cron یا health.sh روی VPS رو چک کنید.")
    return False


OPEN = "open:"
# Incidents whose end is worth one «recovered» line. An answered chat or a quiet error count just closes.
RECOVERY_KINDS = ("disabled_", "paused_", "session_", "heartbeat_", "idle_", "global_", "repeat_flood_")


def _recovered_text(key: str) -> str:
    kind, _, name = key.partition("_")
    if kind == "repeat":
        name = name.removeprefix("flood_")
    words = {
        "disabled": f"{name} دوباره روشنه.",
        "paused": f"محدودیت موقت {name} تموم شد.",
        "session": f"ورود {name} دوباره درسته.",
        "heartbeat": f"heartbeat {name} برگشت.",
        "idle": f"{name} دوباره ارسال می‌کنه.",
        "repeat": f"PEER_FLOODهای پشت‌سرهم {name} تموم شد.",
        "global": "توقف/مکث کلی برداشته شد.",
    }
    return "وضعیت outreach (برطرف شد): " + words.get(kind, f"{key} برطرف شد.")


def run(now: datetime | None = None, *, worker_errors: int = 0, alert: bool = True) -> Result:
    """Check; tell Mohammad once when an incident starts or its state changes, once when it ends; send the daily report
    when due. An unchanged state is never told again (Mohammad, 2026-11-10: the same three lines every hour)."""
    now = now or timezone.now()
    if alert:
        # The check's own heartbeat: the worker's heartbeat endpoint alerts when this goes stale (self_check).
        OutreachHealthEvent.objects.update_or_create(key=RUN_KEY, defaults={"detail": "ok"})
        OutreachHealthEvent.objects.filter(key=RUN_KEY).update(created_at=now)
    result = Result(incidents=check(now, worker_errors=worker_errors))
    # One "open:<key>" row per open incident holds its state fingerprint; the Telegram line goes only when it changes.
    opened = dict(OutreachHealthEvent.objects.filter(key__startswith=OPEN).values_list("key", "detail"))
    seen = set()
    for incident in result.incidents:
        result.restart = result.restart or incident.restart
        mark = f"{OPEN}{incident.key}"[:64]
        seen.add(mark)
        fingerprint = incident.state or incident.key
        if opened.get(mark) == fingerprint:
            continue
        told_recently = mark not in opened and OutreachHealthEvent.objects.filter(key=incident.key, created_at__gte=now - REPEAT_AFTER).exists()
        _row, created = OutreachHealthEvent.objects.update_or_create(key=mark, defaults={"detail": fingerprint})
        if created:
            # Dated by the check's clock: «sent since it opened» is read against this time.
            OutreachHealthEvent.objects.filter(key=mark).update(created_at=now)
        if told_recently:
            # Told within the hour before this rule existed: now open, not told again.
            continue
        _tell(result, incident.key, f"وضعیت outreach: {incident.facts}\nاقدام خودکار: {incident.fix}.", now, alert)
    for mark in set(opened) - seen:
        key = mark[len(OPEN) :]
        if key.startswith(RECOVERY_KINDS) and _still_on(key, now, OutreachHealthEvent.objects.filter(key=mark).values_list("created_at", flat=True).first()):
            # Dropped off the incident list without ending (a pause under 2 hours is quiet, not over): stays open, unsaid.
            continue
        OutreachHealthEvent.objects.filter(key=mark).delete()
        if key.startswith(RECOVERY_KINDS):
            _tell(result, f"recovered_{key}"[:64], _recovered_text(key), now, alert)
    local = timezone.localtime(now, TORONTO)
    if (local.hour, local.minute) >= REPORT_AT:
        start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        if not OutreachHealthEvent.objects.filter(key="daily_report", created_at__gte=start).exists():
            result.report = daily_report(now)
            OutreachHealthEvent.objects.create(key="daily_report", detail=result.report)
            if alert:
                tell_mohammad(result.report)
    return result


def _still_on(key: str, now: datetime, opened_at: datetime | None) -> bool:
    """Whether the condition behind an open incident still holds, read from the DB, not from the incident list (audit
    2026-11-10 23:15: saeid's pause fell under 2 hours, left the list, and «برطرف شد» went out 2 hours early)."""
    kind, _, name = key.partition("_")
    if kind == "global":
        state = OutreachState.load()
        return bool(state.stopped or (state.paused_until and state.paused_until > now))
    if kind == "repeat":
        name = name.removeprefix("flood_")
    account = OutreachAccount.objects.filter(name=name).first()
    if account is None:
        return False
    sent_since = bool(opened_at and account.last_sent_at and account.last_sent_at > opened_at)
    if kind == "disabled":
        return not account.enabled
    if kind == "paused":
        # Over only when the pause has passed or the account has sent since.
        return bool(account.paused_until and account.paused_until > now) and not sent_since
    if kind == "session":
        return not account.session_ok
    if kind == "heartbeat":
        return account.last_heartbeat_at is None or account.last_heartbeat_at < now - HEARTBEAT_STALE
    if kind == "idle":
        # Idle ends when the account sends again, not when a rule (cap, pause, window) explains the silence.
        return not sent_since
    if kind == "repeat":
        return account.events.filter(code="PEER_FLOOD_FREE", created_at__gte=now - timedelta(hours=24)).count() >= REPEATED_FREE_FLOODS
    return False


def _tell(result: Result, key: str, text: str, now: datetime, alert: bool) -> None:
    """Log what was told (the history), dated by the check's own clock, and send it."""
    event = OutreachHealthEvent.objects.create(key=key, detail=text)
    OutreachHealthEvent.objects.filter(pk=event.pk).update(created_at=now)
    result.told.append(text)
    if alert:
        tell_mohammad(text)


def idle_minutes(account: OutreachAccount, start: datetime, end: datetime) -> dict[str, int]:
    """Minutes spent in each not-sending state between start and end, from the STATE log."""
    events = list(account.events.filter(code=STATE, created_at__lt=end).filter(created_at__gte=start).order_by("created_at"))
    before = account.events.filter(code=STATE, created_at__lt=start).first()
    timeline = ([(start, before.detail)] if before else []) + [(event.created_at, event.detail) for event in events]
    minutes: dict[str, int] = defaultdict(int)
    for index, (at, state) in enumerate(timeline):
        until = timeline[index + 1][0] if index + 1 < len(timeline) else end
        if state != "sent":
            minutes[state or "empty"] += int((until - at).total_seconds() // 60)
    return dict(minutes)


def day_facts(account: OutreachAccount, start: datetime, end: datetime) -> dict:
    mine = OutreachMessage.objects.filter(account=account)
    replies_sent = OutreachTurn.objects.filter(
        conversation__account=account, direction=TurnDirection.OUT, telegram_message_id__isnull=False, created_at__gte=start, created_at__lt=end
    ).count()
    return {
        "first": mine.filter(status=OutreachStatus.SENT, sent_at__gte=start, sent_at__lt=end).count(),
        "videos": mine.filter(video_sent_at__gte=start, video_sent_at__lt=end).count(),
        "replies_sent": replies_sent,
        "replied": mine.filter(replied_at__gte=start, replied_at__lt=end).count(),
        "opened": mine.filter(opened_at__gte=start, opened_at__lt=end).count(),
        "failed": mine.filter(status=OutreachStatus.FAILED, updated_at__gte=start, updated_at__lt=end).count(),
        "idle": idle_minutes(account, start, end),
        "flood": OutreachAccountEvent.objects.filter(account=account, code__in=["PEER_FLOOD", "FLOOD_WAIT"], created_at__gte=start, created_at__lt=end).count(),
    }


def daily_report(now: datetime | None = None) -> str:
    """Per account, this Toronto day so far: sends, replies, link opens, idle minutes by reason. DB rows only."""
    now = now or timezone.now()
    local = timezone.localtime(now, TORONTO)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    lines = [f"گزارش روزانه‌ی outreach، {local:%Y-%m-%d} (تا {local:%H:%M} تورنتو)"]
    for account in OutreachAccount.objects.filter(enabled=True).order_by("name"):
        facts = day_facts(account, start, now)
        idle = "، ".join(f"{reason} {minutes} دقیقه" for reason, minutes in sorted(facts["idle"].items(), key=lambda item: -item[1])) or "-"
        lines.append(
            f"{account.name}: پیام اول {facts['first']}، جواب ما {facts['replies_sent']}، ویدیو {facts['videos']}، "
            f"جواب گرفته {facts['replied']}، باز کردن لینک {facts['opened']}، ناموفق {facts['failed']}، flood {facts['flood']}. بیکاری: {idle}."
        )
    return "\n".join(lines)
