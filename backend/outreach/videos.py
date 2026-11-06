"""The intro video, sent only to people who answered the first message (Mohammad, 2026-11-06).

It goes from the same account right after our answer to their reply, with one short line saying it shows how Chamedoon
works, in their language. Someone who answered before this rule gets it after our next answer, never as a push of its
own. Seekers get the seeker video, posters the homeowner one. Never to someone who said no. It counts as one message
toward the account's limits.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.utils import timezone

from outreach import cities
from outreach.models import ConversationState, OutreachAccount, OutreachConversation, OutreachMessage, OutreachStatus
from outreach.services import VIDEO_FOR, is_opted_out

# The video belongs to the exchange: only this soon after our answer to their message.
AFTER_ANSWER = timedelta(minutes=30)
CAPTION = {
    "fa": "این ویدیو کوتاه نشون می‌ده چمدون چطور کار می‌کنه.",
    "en": "This short video shows how Chamedoon works.",
}


def caption_for(message: OutreachMessage) -> str:
    from outreach.agent import writes_english
    from outreach.models import OutreachTurn, TurnDirection

    last = (
        OutreachTurn.objects.filter(conversation__telegram_user_id=message.recipient_telegram_id, direction=TurnDirection.IN)
        .order_by("-created_at")
        .values_list("text", flat=True)
        .first()
    )
    return CAPTION["en" if writes_english(last or "") else "fa"]


def claim_video(account: OutreachAccount, now: datetime) -> OutreachMessage | None:
    """A first message of this account whose person answered, and we just answered them back: its video is due now."""
    answered = OutreachConversation.objects.filter(
        account=account,
        last_inbound_at__isnull=False,
        last_outbound_at__gte=now - AFTER_ANSWER,
        last_outbound_at__lte=now,
    ).exclude(state=ConversationState.STOPPED)
    from outreach.models import OutreachTurn, TurnDirection, TurnStatus

    waiting = set(
        OutreachTurn.objects.filter(direction=TurnDirection.OUT, status__in=[TurnStatus.PENDING, TurnStatus.ANSWERING]).values_list("conversation_id", flat=True)
    )
    # After the whole answer (intro and reply) went, never in between.
    people = [c.telegram_user_id for c in answered if c.last_outbound_at > c.last_inbound_at and c.pk not in waiting]
    owed = OutreachMessage.objects.filter(
        account=account,
        status=OutreachStatus.SENT,
        kind__in=list(VIDEO_FOR),
        video_sent_at__isnull=True,
        video_error="",
        recipient_telegram_id__in=people,
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
