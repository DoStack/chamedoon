"""The intro video that follows a first message, from the same account (Mohammad, 2026-11-05).

Seekers get the seeker video, posters the homeowner one. The worker sends it right after the text when the account
has room for both; a first message that went out before videos existed gets its video as a job of its own. The text
and the video are one touch: no reply after both means no more. Each counts as one message toward the account's limits.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.utils import timezone

from outreach import cities
from outreach.models import ConversationState, OutreachAccount, OutreachConversation, OutreachMessage, OutreachStatus
from outreach.services import VIDEO_FOR, is_opted_out

# A video more than this long after its text would be a second, unasked touch.
CATCH_UP = timedelta(days=3)


def claim_video(account: OutreachAccount, now: datetime) -> OutreachMessage | None:
    """A sent first message of this account still owing its video, inside the recipient's window. Marks nothing."""
    owed = OutreachMessage.objects.filter(
        account=account,
        status=OutreachStatus.SENT,
        kind__in=list(VIDEO_FOR),
        video_sent_at__isnull=True,
        video_error="",
        sent_at__gte=now - CATCH_UP,
        sent_at__lte=now - timedelta(minutes=1),
    ).order_by("sent_at")
    for message in owed:
        if is_opted_out(username=message.recipient_username, telegram_user_id=message.recipient_telegram_id):
            record_video(message.pk, ok=False, error="opted_out")
            continue
        stopped = OutreachConversation.objects.filter(telegram_user_id=message.recipient_telegram_id, state=ConversationState.STOPPED).exists()
        if stopped:
            record_video(message.pk, ok=False, error="chat_stopped")
            continue
        if not cities.in_send_window(message.country or "CA", message.city or "Toronto", now):
            continue
        # Claimed once: a worker that dies mid-send never sends it twice.
        OutreachMessage.objects.filter(pk=message.pk).update(video_error="claimed", updated_at=now)
        message.video_only = True
        return message
    return None


def record_video(pk: int, *, ok: bool, error: str = "", now: datetime | None = None) -> OutreachMessage | None:
    message = OutreachMessage.objects.filter(pk=pk).first()
    if message is None:
        return None
    if ok:
        message.video_sent_at = now or timezone.now()
        message.video_error = ""
    else:
        message.video_error = (error or "failed")[:64]
    message.save(update_fields=["video_sent_at", "video_error", "updated_at"])
    # Telegram limiting a video limits the account, exactly as for a text.
    account = message.account
    if not ok and account is not None:
        from outreach import accounts

        moment = now or timezone.now()
        if error == "PEER_FLOOD":
            accounts.record_peer_flood(account, moment)
        elif error.startswith("FLOOD_WAIT_") and error[len("FLOOD_WAIT_"):].isdigit():
            accounts.record_flood_wait(account, int(error[len("FLOOD_WAIT_"):]), moment)
    return message
