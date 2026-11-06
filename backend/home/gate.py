"""The contact gate: the one place that decides whether a person may see how to reach a listing's advertiser.

The API and the old pages call `reveal`. Bot cards and alert messages hold no contact at all while the gate is on;
their contact button opens the listing in the Mini App, which ends up here.

A listing's contact needs membership of its channel (`public.gate_channel`) and of that channel's group, when it has one.
A member answer holds for 24 hours. When Telegram cannot answer, or takes longer than two seconds, the person gets
through anyway and the failure is logged and counted (decision 2026-11-02). A listing with no channel to ask for stays closed.

The same check runs before a new post (`join_check`), behind its own switch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from home import bot, conf
from home.models import ChannelMembership, ContactReveal, GateChat, HomeChannel, HomeListing, MembershipStatus
from home.public import ACTIVE, GONE, contact_handle, gate_channel, has_source, listing_state
from telemetry import capture_user

logger = logging.getLogger(__name__)

REVEAL_CAP = 30
REVEAL_WINDOW = timedelta(hours=24)
MEMBER_TRUSTED_FOR = timedelta(hours=24)
MEMBER_CHECK_TIMEOUT_SECONDS = 2
INVITE_LINK_NAME = "Chamedoon app"
FAILED_OPEN_ALERT_AT = 3
IN_CHANNEL = {"creator", "administrator", "member"}
# Telegram's way of saying it has never seen this person in the chat, not that it failed to answer.
NEVER_MET = ("user not found", "participant_id_invalid")

GATE_MEMBER = "member"
GATE_FAILED_OPEN = "failed_open"
GATE_OFF = "gate_off"
GATE_OWNER = "owner"


class GateRefused(Exception):
    """The contact stays closed. `code` is the API's error code; the old pages use it too.

    `missing` names the chats still to join under `not_member`: channel, group or both."""

    def __init__(self, code: str, *, channel: HomeChannel | None = None, missing: list[str] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.channel = channel
        self.missing = missing if missing is not None else [str(GateChat.CHANNEL)]


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


def join_url(channel: HomeChannel, chat: str = GateChat.CHANNEL) -> str:
    """The one-tap link behind the join button. Empty when a private group has no link and the bot could not make one."""
    if chat == GateChat.CHANNEL:
        return f"https://t.me/{channel.username}"
    if channel.group_username:
        return f"https://t.me/{channel.group_username}"
    if channel.group_invite_link:
        return channel.group_invite_link
    try:
        # A link of its own, so the group's primary link is never revoked. Its joins show apart in Telegram's link stats.
        made = bot.call("createChatInviteLink", {"chat_id": channel.group_target(), "name": INVITE_LINK_NAME}, timeout=MEMBER_CHECK_TIMEOUT_SECONDS)
    except bot.BotError as exc:
        logger.warning("gate_invite_link_failed channel=%s reason=%s", channel.username, (exc.description or "")[:120])
        return ""
    link = str(made.get("invite_link") or "") if isinstance(made, dict) else ""
    if link:
        channel.group_invite_link = link[:128]
        HomeChannel.objects.filter(pk=channel.pk).update(group_invite_link=channel.group_invite_link)
    return link


def chat_title(channel: HomeChannel, chat: str = GateChat.CHANNEL) -> str:
    from home.public import channel_title

    if chat == GateChat.CHANNEL:
        return channel_title(channel)
    return channel.group_title or f"گروه {channel_title(channel)}"


def chat_rows(channel: HomeChannel, missing: list[str]) -> list[dict]:
    """The join sheet's rows, channel first. A group with no link to join is left out rather than shown as a dead button."""
    rows = []
    for chat in (GateChat.CHANNEL, GateChat.GROUP):
        if chat not in missing:
            continue
        url = join_url(channel, chat)
        if url:
            rows.append({"kind": str(chat), "title": chat_title(channel, chat), "join_url": url, "photo_url": f"/api/v1/chats/{channel.pk}/{chat}/photo/"})
    return rows


def _ask_telegram(user, channel: HomeChannel, chat: str = GateChat.CHANNEL) -> str | None:
    """member, not_member or banned, or None when Telegram could not answer."""
    target = channel.target() if chat == GateChat.CHANNEL else channel.group_target()
    try:
        result = bot.call(
            "getChatMember",
            {"chat_id": target, "user_id": user.telegram_user_id},
            timeout=MEMBER_CHECK_TIMEOUT_SECONDS,
        )
    except bot.BotError as exc:
        description = (exc.description or "").lower()
        if any(phrase in description for phrase in NEVER_MET):
            return MembershipStatus.NOT_MEMBER
        reason = (exc.description or "")[:120]
        logger.warning("gate_failed_open channel=%s chat=%s user=%s reason=%s", channel.username, chat, user.pk, reason)
        HomeChannel.objects.filter(pk=channel.pk).update(last_error=f"Membership check failed ({chat}): {reason}"[:300])
        return None
    status = (result or {}).get("status") if isinstance(result, dict) else None
    if status in IN_CHANNEL or (status == "restricted" and result.get("is_member")):
        return MembershipStatus.MEMBER
    if status == "kicked":
        return MembershipStatus.BANNED
    return MembershipStatus.NOT_MEMBER


def membership(user, channel: HomeChannel, chat: str = GateChat.CHANNEL) -> str | None:
    """This person's standing in the channel or its group. Only a member answer is reused; anyone else is asked again each time."""
    saved = ChannelMembership.objects.filter(user=user, channel=channel, chat=chat).first()
    now = timezone.now()
    if saved and saved.status == MembershipStatus.MEMBER and now - saved.checked_at < MEMBER_TRUSTED_FOR:
        return MembershipStatus.MEMBER
    status = _ask_telegram(user, channel, chat)
    if status is not None:
        ChannelMembership.objects.update_or_create(user=user, channel=channel, chat=chat, defaults={"status": status, "checked_at": now})
    return status


@dataclass
class Standing:
    """The person against a channel and its group. `failed`: Telegram did not answer for one of them, so the gate lets them through."""

    missing: list[str]
    banned: bool
    failed: bool

    @property
    def through(self) -> bool:
        return self.failed or not self.missing


def standing(user, channel: HomeChannel) -> Standing:
    chats = [GateChat.CHANNEL, *([GateChat.GROUP] if channel.has_group() else [])]
    missing, banned, failed = [], False, False
    for chat in chats:
        status = membership(user, channel, chat)
        if status is None:
            failed = True
        elif status == MembershipStatus.BANNED:
            banned = True
        elif status == MembershipStatus.NOT_MEMBER:
            missing.append(str(chat))
    return Standing(missing=missing, banned=banned, failed=failed)


def join_check(user, *, country: str, city: str) -> dict:
    """Before a new post: the chats of that city still to join. Empty `chats` means go ahead.

    Off, no channel for the place, a Telegram error or a ban (the poster cannot join anyway) all let the post through."""
    from home.public import gate_channel

    if not conf.join_gate_poster_on():
        return {"chats": [], "gate": GATE_OFF}
    channel = gate_channel(HomeListing(country=country.upper()[:2], city=city[:64]))
    if channel is None:
        return {"chats": [], "gate": "no_channel"}
    found = standing(user, channel)
    if found.failed:
        capture_user("join_check_failed", user, source="api", side="housing", properties={"moment": "post", "channel": channel.username})
        return {"chats": [], "gate": GATE_FAILED_OPEN}
    rows = [] if found.banned else chat_rows(channel, found.missing)
    return {"chats": rows, "gate": "not_member" if rows else GATE_MEMBER}


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
    return contact_handle(listing), listing.contact_phone or "", source_url


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
    from home.ownership import owns

    if owns(listing, user.telegram_user_id):
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
    found = standing(user, channel)
    if found.banned:
        raise GateRefused("gate_banned", channel=channel)
    # Only a group the bot cannot link to is missing: nothing to tap, so the person goes through.
    if not found.through and chat_rows(channel, found.missing):
        raise GateRefused("not_member", channel=channel, missing=found.missing)
    gate = GATE_FAILED_OPEN if found.failed else GATE_MEMBER
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
