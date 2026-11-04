"""The contact gate: the one place that decides whether a person may see how to reach a listing's advertiser.

The API and the old pages call `reveal`. Bot cards and alert messages hold no contact at all while the gate is on;
their contact button opens the listing in the Mini App, which ends up here.

A listing's contact needs membership of its channel (`public.gate_channel`). A member answer holds for 24 hours.
When Telegram cannot answer, the contact opens anyway and the failure is logged and counted (decision 2026-11-02).
A listing with no channel to ask for stays closed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from home import bot, conf
from home.models import ChannelMembership, ContactReveal, HomeChannel, HomeListing, MembershipStatus
from home.public import ACTIVE, GONE, gate_channel, has_source, listing_state
from telemetry import capture_user

logger = logging.getLogger(__name__)

REVEAL_CAP = 30
REVEAL_WINDOW = timedelta(hours=24)
MEMBER_TRUSTED_FOR = timedelta(hours=24)
MEMBER_CHECK_TIMEOUT_SECONDS = 5
FAILED_OPEN_ALERT_AT = 3
IN_CHANNEL = {"creator", "administrator", "member"}
# Telegram's way of saying it has never seen this person in the chat, not that it failed to answer.
NEVER_MET = ("user not found", "participant_id_invalid")

GATE_MEMBER = "member"
GATE_FAILED_OPEN = "failed_open"
GATE_OFF = "gate_off"
GATE_OWNER = "owner"


class GateRefused(Exception):
    """The contact stays closed. `code` is the API's error code; the old pages use it too."""

    def __init__(self, code: str, *, channel: HomeChannel | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.channel = channel


@dataclass
class Reveal:
    telegram: str
    phone: str
    source_url: str
    gate: str
    reveals_left: int

    @property
    def has_any(self) -> bool:
        return bool(self.telegram or self.phone or self.source_url)

    @property
    def membership_checked(self) -> bool:
        return self.gate == GATE_MEMBER

    @property
    def telegram_url(self) -> str:
        return f"https://t.me/{self.telegram}" if self.telegram else ""


def join_url(channel: HomeChannel) -> str:
    return f"https://t.me/{channel.username}"


def _ask_telegram(user, channel: HomeChannel) -> str | None:
    """member, not_member or banned, or None when Telegram could not answer."""
    try:
        result = bot.call(
            "getChatMember",
            {"chat_id": channel.target(), "user_id": user.telegram_user_id},
            timeout=MEMBER_CHECK_TIMEOUT_SECONDS,
        )
    except bot.BotError as exc:
        description = (exc.description or "").lower()
        if any(phrase in description for phrase in NEVER_MET):
            return MembershipStatus.NOT_MEMBER
        reason = (exc.description or "")[:120]
        logger.warning("gate_failed_open channel=%s user=%s reason=%s", channel.username, user.pk, reason)
        HomeChannel.objects.filter(pk=channel.pk).update(last_error=f"Membership check failed: {reason}"[:300])
        return None
    status = (result or {}).get("status") if isinstance(result, dict) else None
    if status in IN_CHANNEL or (status == "restricted" and result.get("is_member")):
        return MembershipStatus.MEMBER
    if status == "kicked":
        return MembershipStatus.BANNED
    return MembershipStatus.NOT_MEMBER


def membership(user, channel: HomeChannel) -> str | None:
    """This person's standing in the channel. Only a member answer is reused; anyone else is asked again each time."""
    saved = ChannelMembership.objects.filter(user=user, channel=channel).first()
    now = timezone.now()
    if saved and saved.status == MembershipStatus.MEMBER and now - saved.checked_at < MEMBER_TRUSTED_FOR:
        return MembershipStatus.MEMBER
    status = _ask_telegram(user, channel)
    if status is not None:
        ChannelMembership.objects.update_or_create(user=user, channel=channel, defaults={"status": status, "checked_at": now})
    return status


def _reveals_in_window(user):
    return ContactReveal.objects.filter(user=user, created_at__gte=timezone.now() - REVEAL_WINDOW)


def _record(user, listing: HomeListing, *, failed_open: bool) -> int:
    """Count this reveal unless this listing is already in the window. Returns how many are left. Safe to retry."""
    window = _reveals_in_window(user)
    used = window.count()
    if not window.filter(listing=listing).exists():
        if used >= REVEAL_CAP:
            logger.warning("gate_reveal_cap user=%s", user.pk)
            raise GateRefused("reveal_cap")
        try:
            with transaction.atomic():
                _row, created = ContactReveal.objects.get_or_create(
                    user=user, listing=listing, day=timezone.now().date(), defaults={"failed_open": failed_open}
                )
        except IntegrityError:
            created = False
        used += 1 if created else 0
    return max(0, REVEAL_CAP - used)


def _contact(listing: HomeListing) -> tuple[str, str, str]:
    source_url = listing.post.source_url if has_source(listing) else ""
    return (listing.contact_telegram or "").lstrip("@"), listing.contact_phone or "", source_url


def reveal(user, listing: HomeListing, *, source: str = "api") -> Reveal:
    """Everything a person may see of a listing's contact, or GateRefused with the reason."""
    try:
        found = _reveal(user, listing)
    except GateRefused as refused:
        capture_user("gate_denied", user, source=source, side="housing", properties={"listing_id": listing.pk, "reason": refused.code})
        raise
    capture_user("contact_revealed", user, source=source, side="housing", properties={"listing_id": listing.pk, "gate": found.gate, "has_any": found.has_any, "reveals_left": found.reveals_left})
    return found


def _reveal(user, listing: HomeListing) -> Reveal:
    state = listing_state(listing)
    if state == GONE:
        raise GateRefused("listing_gone")
    if listing.post.author_id == user.telegram_user_id:
        return Reveal(*_contact(listing), gate=GATE_OWNER, reveals_left=max(0, REVEAL_CAP - _reveals_in_window(user).count()))
    if state != ACTIVE:
        raise GateRefused("listing_closed")

    if not conf.contact_gate_on():
        # Off means off: no Telegram call, no cap, no row.
        return Reveal(*_contact(listing), gate=GATE_OFF, reveals_left=REVEAL_CAP)
    channel = gate_channel(listing)
    if channel is None:
        logger.warning("gate_no_channel listing=%s country=%s", listing.pk, listing.country)
        raise GateRefused("no_channel")
    status = membership(user, channel)
    if status == MembershipStatus.BANNED:
        raise GateRefused("gate_banned", channel=channel)
    if status == MembershipStatus.NOT_MEMBER:
        raise GateRefused("not_member", channel=channel)
    gate = GATE_MEMBER if status == MembershipStatus.MEMBER else GATE_FAILED_OPEN
    left = _record(user, listing, failed_open=gate == GATE_FAILED_OPEN)
    return Reveal(*_contact(listing), gate=gate, reveals_left=left)


def failed_open_report() -> dict:
    """For System health: with the gate on, how many contacts opened in the last 24 hours because Telegram did not answer.

    A bot that lost admin rights opens every contact, so this turns red when failures are the usual answer.
    """
    if not conf.contact_gate_on():
        return {"on": False, "reveals": 0, "failed_open": 0, "problem": False, "channels": []}
    since = timezone.now() - REVEAL_WINDOW
    reveals = ContactReveal.objects.filter(created_at__gte=since)
    total = reveals.count()
    failed = reveals.filter(failed_open=True).count()
    return {
        "on": True,
        "reveals": total,
        "failed_open": failed,
        "problem": failed >= FAILED_OPEN_ALERT_AT and failed * 2 >= total,
        "channels": list(HomeChannel.objects.filter(last_error__startswith="Membership check failed").values_list("username", flat=True)),
    }
