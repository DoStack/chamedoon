"""Messages after a chat or a sign-up (spec section 4). Nobody who never answered gets one.

- P5 «هنوز موجوده؟» on day 12 of an ad: the bot writes to a member, the same account to someone who chatted.
- P3 the week's numbers, only when they are real: 5 unique views, 1 save or 1 contact taken.
- A contact taken: the bot tells a member who started it, at most once a day per ad.
All of it waits for `followups_enabled` in Outreach settings.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta

from django.utils import timezone

from home import alert_store, bot, conf as home_conf, deeplink
from home.alerts import city_fa
from home.models import HomeListing, ListingKind, ListingStatus
from home.ownership import owned_listings, owner_telegram_id
from outreach import cities, conf, templates
from outreach.models import ConversationState, OutreachConversation, OutreachKind, OutreachMessage, OutreachStatus

logger = logging.getLogger(__name__)

P5_AFTER = timedelta(days=12)
P3_EVERY = timedelta(days=7)
P3_MIN_VIEWS = 5
CONTACT_NOTICE_EVERY = timedelta(hours=24)


def enabled() -> bool:
    return bool(conf.row().followups_enabled)


def _link(listing: HomeListing, token: str = "") -> str:
    from outreach.cities import metro_of_listing

    channel = metro_of_listing(listing)
    return home_conf.mini_app_link(start=deeplink.build(listing.country, channel.city if channel else listing.city, listing=listing.pk, source=f"o-{token}" if token else ""))


def _recent(listing: HomeListing, kind: str, since: datetime) -> bool:
    return OutreachMessage.objects.filter(listing=listing, kind=kind, created_at__gte=since).exclude(status=OutreachStatus.SKIPPED).exists()


def _p5_due(listing: HomeListing, now: datetime) -> bool:
    confirmed = listing.last_confirmed_at
    return (
        listing.kind == ListingKind.OFFER
        and listing.status == ListingStatus.ACTIVE
        and listing.first_seen <= now - P5_AFTER
        and (confirmed is None or confirmed <= now - P5_AFTER)
        and not _recent(listing, OutreachKind.P5, now - P5_AFTER)
    )


def _p3_numbers(listing: HomeListing) -> dict | None:
    from home.stats import counts

    week = counts(listing, since=timezone.now() - P3_EVERY)
    if week["views"] >= P3_MIN_VIEWS or week["saves"] or week["contacts"]:
        return week
    return None


def _city(listing: HomeListing) -> str:
    from outreach.cities import metro_of_listing

    channel = metro_of_listing(listing)
    return channel.city if channel else listing.city


def build(result, *, now: datetime | None = None, dry_run: bool = False) -> None:
    from outreach.services import is_opted_out, new_message_status, new_token

    if not enabled():
        return
    now = now or timezone.now()
    people: dict[int, OutreachConversation | None] = {
        conversation.telegram_user_id: conversation
        for conversation in OutreachConversation.objects.filter(state=ConversationState.OPEN).select_related("account", "first_message")
    }
    for telegram_id in OutreachMessage.objects.filter(status=OutreachStatus.SENT, member_at__isnull=False).values_list("recipient_telegram_id", flat=True):
        if telegram_id:
            people.setdefault(telegram_id, None)
    for telegram_id, conversation in people.items():
        username = conversation.username if conversation else ""
        if is_opted_out(username=username, telegram_user_id=telegram_id):
            continue
        by_bot = alert_store.bot_open(telegram_id) and not alert_store.bot_blocked(telegram_id)
        if conversation is None and not by_bot:
            continue
        for listing in owned_listings(telegram_id).filter(status=ListingStatus.ACTIVE, kind=ListingKind.OFFER):
            plan = None
            if _p5_due(listing, now):
                plan = (OutreachKind.P5, None)
            elif not _recent(listing, OutreachKind.P3, now - P3_EVERY):
                numbers = _p3_numbers(listing)
                if numbers is not None:
                    plan = (OutreachKind.P3, numbers)
            if plan is None:
                continue
            if conversation is not None and not answered_last(conversation):
                # Our last message there is unanswered: no proactive message on top of it (rule 18).
                continue
            if by_bot and not cities.in_send_window(listing.country, _city(listing), now):
                # The bot also writes only in the person's 10:00 to 20:00 (rule 15); the next build tries again.
                continue
            kind, numbers = plan
            token = new_token()
            name = templates.valid_name(conversation.first_message.recipient_name if conversation and conversation.first_message else "", username)
            if kind == OutreachKind.P5:
                text, variant = templates.p5(random.Random(token), name, _city(listing))
            else:
                text, variant = templates.p3(numbers["views"], numbers["saves"], numbers["contacts"], _link(listing, token))
            message = OutreachMessage(
                kind=kind,
                recipient_username=username or "",
                recipient_telegram_id=telegram_id,
                listing=listing,
                country=listing.country,
                city=_city(listing),
                source_at=now,
                variant=variant,
                extra={"by": "bot" if by_bot else "account", **(numbers or {})},
                token=token,
                text=text,
            )
            if by_bot:
                # Free and asked for: the bot writes now.
                message.status = OutreachStatus.SENT
                if not dry_run:
                    message.sent_at = now
                    message.save()
                    _send_by_bot(message)
            else:
                message.conversation = conversation
                message.account = conversation.account
                message.status = new_message_status()
                message.error_code = "awaiting_yes" if message.status == OutreachStatus.HELD else ""
                if not dry_run:
                    message.save()
            result.created.append(message)


def answered_last(conversation: OutreachConversation) -> bool:
    """The person spoke after our last message, or there is no message of ours yet."""
    last = conversation.turns.order_by("-created_at").values_list("direction", flat=True).first()
    return last != "out"


def _send_by_bot(message: OutreachMessage) -> None:
    from outreach import guard

    try:
        text = guard.check(message.text)
    except guard.Blocked as exc:
        OutreachMessage.objects.filter(pk=message.pk).update(status=OutreachStatus.SKIPPED, error_code=f"blocked_{exc.reason}"[:64])
        return
    try:
        ids = bot.send_message(message.recipient_telegram_id, text)
    except bot.BotError as exc:
        OutreachMessage.objects.filter(pk=message.pk).update(status=OutreachStatus.FAILED, error_code=f"BOT_{exc.code}"[:64], error_detail=exc.description[:500])
        return
    OutreachMessage.objects.filter(pk=message.pk).update(telegram_message_id=ids[0] if ids else None)


def _url(text: str) -> str:
    import re

    found = re.search(r"\((https://t\.me/[^)\s]+)\)", text)
    return found.group(1) if found else ""


def skip_reason(message: OutreachMessage, now: datetime) -> str:
    """A follow-up still makes sense: the ad is live, they did not stop, and it is not too late."""
    from outreach.services import is_opted_out

    if message.listing is None or message.listing.status != ListingStatus.ACTIVE:
        return "listing_closed"
    if is_opted_out(username=message.recipient_username, telegram_user_id=message.recipient_telegram_id):
        return "opted_out"
    if message.conversation_id and message.conversation.state != ConversationState.OPEN:
        return "chat_closed"
    if message.conversation_id and not answered_last(message.conversation):
        return "unanswered"
    if message.created_at and message.created_at < now - timedelta(days=3):
        return "stale"
    return ""


def contact_taken(listing: HomeListing) -> None:
    """Someone took the contact of a crawled ad whose owner started the bot: tell them, once a day at most."""
    if not enabled():
        return
    owner = owner_telegram_id(listing.post)
    if not owner or not alert_store.bot_open(owner) or alert_store.bot_blocked(owner):
        return
    flags = dict(HomeListing.objects.filter(pk=listing.pk).values_list("flags", flat=True).first() or {})
    last = flags.get("contact_notice_at")
    now = timezone.now()
    if last and datetime.fromisoformat(last) > now - CONTACT_NOTICE_EVERY:
        return
    flags["contact_notice_at"] = now.isoformat()
    HomeListing.objects.filter(pk=listing.pk).update(flags=flags)
    text = f"یه نفر اطلاعات تماس آگهی خونه‌تون تو {city_fa(_city(listing))} رو گرفت.\n{_link(listing)}"
    try:
        bot.send_message(owner, text)
    except bot.BotError as exc:
        logger.info("contact_notice_failed listing=%s error=%s", listing.pk, exc.description)
