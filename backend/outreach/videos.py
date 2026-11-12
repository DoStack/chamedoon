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
# Persian only, to everyone (Mohammad, 2026-11-06).
CAPTION = "این ویدیو کوتاه نشون می‌ده چمدون چطور کار می‌کنه."


def caption_for(message: OutreachMessage) -> str:
    """The line, then their own ad link (a poster with a live ad) and the bot handle: every video carries the bot
    (Mohammad, 2026-11-11). A link or a line the chat already has is left out (audit 2026-11-11 16:15, conv 32 T208
    repeated T207's link and bot line word for word)."""
    from home.models import ListingKind, ListingStatus
    from outreach import agent, completion
    from outreach.models import OutreachKind

    seeker = message.kind in {OutreachKind.S1, OutreachKind.S1B, OutreachKind.S2}
    lines = [CAPTION]
    listing = message.listing
    if not seeker and listing is not None and listing.kind == ListingKind.OFFER and listing.status == ListingStatus.ACTIVE:
        lines.append(f"اینم لینک آگهی‌تون: {completion.view_link(listing, message.token)}")
    lines.append(agent.bot_line(seeker))
    sent = _sent(message)
    said = {_key(line) for text in sent for line in text.split("\n") if _key(line)}
    from outreach.guard import URL

    urls = {url for text in sent for url in URL.findall(text)}
    kept = [lines[0]] + [line for line in lines[1:] if _key(line) not in said and not any(url in urls for url in URL.findall(line))]
    return "\n".join(kept)


def _key(line: str) -> str:
    import re

    return re.sub(r"\s+", "", line or "")


def _sent(message: OutreachMessage) -> list[str]:
    """Every text we already sent in this person's chat on this account."""
    from outreach.models import TurnDirection

    conversation = _conversation(message)
    if conversation is None:
        return [message.text] if message.text else []
    texts = list(conversation.turns.filter(direction=TurnDirection.OUT).exclude(action="video").values_list("text", flat=True))
    return [text for text in [message.text, *texts] if text]


def _conversation(message: OutreachMessage) -> OutreachConversation | None:
    if not message.recipient_telegram_id:
        return None
    return OutreachConversation.objects.filter(telegram_user_id=message.recipient_telegram_id, account=message.account).first()


def _waits_after_intro(conversation: OutreachConversation) -> bool:
    """Our intro went and they have not written since: the video waits for their next message (audit 2026-11-11 16:15,
    conv 32 T208 went 73 s after the intro T207 with nothing from her in between, rule 35)."""
    from outreach import agent
    from outreach.models import TurnDirection

    intros = [text for text in agent.INTRO_TEXTS.values() if text]
    outs = conversation.turns.filter(direction=TurnDirection.OUT).exclude(action="video").order_by("-created_at").values_list("text", "created_at")
    last_intro = next((at for text, at in outs if any(intro in (text or "") for intro in intros)), None)
    if last_intro is None:
        return False
    return not conversation.turns.filter(direction=TurnDirection.IN, created_at__gt=last_intro).exists()


def _last_out_action(conversation: OutreachConversation) -> str:
    from outreach.models import TurnDirection

    return conversation.turns.filter(direction=TurnDirection.OUT).order_by("-created_at").values_list("action", flat=True).first() or ""


def _outs_since_their_last(conversation: OutreachConversation) -> int:
    from outreach.models import TurnDirection

    last_in = conversation.turns.filter(direction=TurnDirection.IN).order_by("-created_at").values_list("created_at", flat=True).first()
    outs = conversation.turns.filter(direction=TurnDirection.OUT)
    return (outs.filter(created_at__gt=last_in) if last_in else outs).count()


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
    # Never right after a message Mohammad wrote, and never to someone we asked for their own photos or video (audit 11-08).
    people = [
        c.telegram_user_id
        for c in answered
        if c.last_outbound_at > c.last_inbound_at and c.pk not in waiting and not (c.awaiting or {}).get("media") and _last_out_action(c) != "operator" and _outs_since_their_last(c) < 2 and not _waits_after_intro(c)
    ]
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


def _video_turn(message: OutreachMessage, telegram_message_id: int | None, at: datetime) -> None:
    """The video is a turn of the chat at the moment it went, so the order, the window and the unanswered check read
    the chat right (audit 2026-11-08: T60 and T84 were backfilled later, after the person's reply)."""
    from outreach.models import OutreachConversation, OutreachTurn, TurnDirection, TurnStatus

    conversation = OutreachConversation.objects.filter(telegram_user_id=message.recipient_telegram_id, account=message.account).first()
    if conversation is None:
        return
    caption = caption_for(message)
    if telegram_message_id and conversation.turns.filter(direction=TurnDirection.OUT, telegram_message_id=telegram_message_id).exists():
        # The worker's own outgoing event may have stored the caption first as a hand-written line: it is the video.
        conversation.turns.filter(direction=TurnDirection.OUT, telegram_message_id=telegram_message_id, action="by_person").update(action="video")
        return
    OutreachTurn.objects.create(
        conversation=conversation, direction=TurnDirection.OUT, text=caption, telegram_message_id=telegram_message_id, status=TurnStatus.DONE, action="video"
    )
    conversation.last_outbound_at = at
    conversation.save(update_fields=["last_outbound_at", "updated_at"])


def record_video(pk: int, *, ok: bool, error: str = "", now: datetime | None = None, telegram_message_id: int | None = None) -> OutreachMessage | None:
    message = OutreachMessage.objects.filter(pk=pk).first()
    if message is None:
        return None
    if ok:
        message.video_sent_at = now or timezone.now()
        message.video_error = ""
        _video_turn(message, telegram_message_id, message.video_sent_at)
    else:
        message.video_error = (error or "failed")[:64]
    message.save(update_fields=["video_sent_at", "video_error", "updated_at"])
    # Telegram limiting a video limits the account, exactly as for a text.
    account = message.account
    if not ok and account is not None:
        from outreach import accounts

        moment = now or timezone.now()
        if error.startswith("PEER_FLOOD"):
            accounts.record_peer_flood(account, moment, error.removeprefix("PEER_FLOOD").lstrip(":"))
        elif error.startswith("FLOOD_WAIT_") and error[len("FLOOD_WAIT_"):].isdigit():
            accounts.record_flood_wait(account, int(error[len("FLOOD_WAIT_"):]), moment)
    return message
