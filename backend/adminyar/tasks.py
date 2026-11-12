"""The human admin's share of the outreach queue: same queue, same per-account caps and pace, same PEER_FLOOD lock.

Plan section 2: one queue and one claim per recipient, a shared daily cap per account (the agent's own gate counts
human sends too, because they are ordinary OutreachMessage rows of that account), and PEER_FLOOD from either side
locks the account for first messages.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from outreach import accounts
from outreach.models import OutreachAccount, OutreachMessage, OutreachStatus, SentBy
from outreach.services import PEER_FLOOD, OutreachConflict, claim_next_message, record_result

LOCAL = ZoneInfo("America/Toronto")
LIMITED = "LIMITED_BY_ADMIN"

# Failed buttons: label code -> (outcome, error_code). PEER_FLOOD goes the agent's way: the person is never retried
# from another account, and the account is locked for first messages.
FAILURES = {
    "flood": ("retry", PEER_FLOOD),
    "notfound": ("failed", "human_not_found"),
    "blocked": ("failed", "human_blocked"),
}


def account_for(admin) -> OutreachAccount | None:
    if not admin.team_account:
        return None
    return OutreachAccount.objects.filter(name=admin.team_account).first()


def claim(account_name: str, now: datetime | None = None, *, build: bool = True) -> tuple[OutreachMessage | None, str]:
    """The next first message for the human on this account, or None and why not.

    Never refused for caps, pace, a pause or the send window (Mohammad, 2026-11-12): the human keeps working. The
    send still counts on the account, so the agent's own gate backs off it. The template text is kept as approved.
    When the queue is empty, the queue is built once and tried again.
    """
    now = now or timezone.now()
    message, reason = _claim(account_name, now)
    if message is None and reason == "empty" and build:
        from outreach.services import build_outreach_queue

        build_outreach_queue(now=now)
        message, reason = _claim(account_name, now)
    return message, reason


def _claim(account_name: str, now: datetime) -> tuple[OutreachMessage | None, str]:
    with transaction.atomic():
        account = OutreachAccount.objects.select_for_update().filter(name=account_name).first()
        if account is None:
            return None, "no_account"
        message, reason = claim_next_message(now, account=account, rewrite=False, human=True)
        if message is None:
            return None, reason
        message.account = account
        message.sent_by = SentBy.HUMAN
        message.save(update_fields=["account", "sent_by", "updated_at"])
        # The agent's pace starts again from this send: it backs off the account the human is using.
        account.last_sent_at = now
        account.next_send_at = now + accounts.next_pause(account)
        account.save(update_fields=["last_sent_at", "next_send_at"])
    accounts.note_state(account, "sent_by_human")
    return message, ""


def open_claim(account_name: str) -> OutreachMessage | None:
    """A human claim on this account still waiting for Sent or Failed."""
    return (
        OutreachMessage.objects.filter(account__name=account_name, sent_by=SentBy.HUMAN, status=OutreachStatus.SENDING)
        .order_by("claimed_at")
        .first()
    )


def report(message_id: int, account_name: str, outcome: str, now: datetime | None = None) -> str:
    """Sent or one of FAILURES for a human claim of this account. Returns 'ok', 'gone' or 'unknown'."""
    message = OutreachMessage.objects.select_related("account").filter(pk=message_id, account__name=account_name, sent_by=SentBy.HUMAN).first()
    if message is None:
        return "gone"
    try:
        if outcome == "sent":
            record_result(message, outcome="sent", now=now)
        elif outcome in FAILURES:
            kind, code = FAILURES[outcome]
            # PEER_FLOOD's detail is read as the SpamBot answer, so it stays empty; the code says it was the human.
            detail = "" if code == PEER_FLOOD else "reported by the human admin"
            record_result(message, outcome=kind, error_code=code, error_detail=detail, now=now)
        else:
            return "unknown"
    except OutreachConflict:
        return "gone"
    return "ok"


def next_local_morning(now: datetime) -> datetime:
    local = now.astimezone(LOCAL)
    tomorrow = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return tomorrow.astimezone(ZoneInfo("UTC"))


def mark_limited(account_name: str, now: datetime | None = None) -> datetime | None:
    """⛔: the agent sends no first messages from this account until the next local day. The human goes on."""
    now = now or timezone.now()
    account = OutreachAccount.objects.filter(name=account_name).first()
    if account is None:
        return None
    until = next_local_morning(now)
    accounts._log(account, LIMITED, "admin tapped: account limited")
    accounts._pause(account, until, LIMITED)
    account.refresh_from_db(fields=["paused_until"])
    return account.paused_until


def sent_today(account_name: str, now: datetime | None = None) -> dict:
    now = now or timezone.now()
    since = now - timedelta(hours=24)
    rows = OutreachMessage.objects.filter(account__name=account_name, claimed_at__gte=since)
    return {
        "human": rows.filter(sent_by=SentBy.HUMAN, status=OutreachStatus.SENT).count(),
        "agent": rows.filter(sent_by=SentBy.AGENT, status=OutreachStatus.SENT).count(),
    }


REMIND_AFTER = timedelta(hours=1)


def due_reminders(now: datetime | None = None) -> list[OutreachMessage]:
    """Human claims open for over an hour and not yet reminded. Unreported at HUMAN_LEASE, they fail as contacted."""
    now = now or timezone.now()
    rows = OutreachMessage.objects.filter(sent_by=SentBy.HUMAN, status=OutreachStatus.SENDING, claimed_at__lt=now - REMIND_AFTER).select_related("account")
    return [row for row in rows if not (row.extra or {}).get("adminyar_reminded")]


def mark_reminded(message: OutreachMessage) -> None:
    message.extra = {**(message.extra or {}), "adminyar_reminded": True}
    message.save(update_fields=["extra", "updated_at"])
