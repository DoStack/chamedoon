from __future__ import annotations

import logging

from matching.contact import SYNTHETIC_TELEGRAM_USER_ID, intro_draft_for_match
from matching.models import Match
from notifications.messages import (
    close_listing_prompt_text,
    connected_text,
    match_accepted_text,
    match_cancelled_text,
    match_rejected_text,
    new_match_text,
    support_auto_closed_text,
    support_reply_text,
)
from support.models import SupportTicket
from notifications.telegram import (
    close_listing_markup,
    connected_markup,
    open_koolbar_markup,
    send_telegram_message,
)

logger = logging.getLogger(__name__)


def notify_new_match(match: Match) -> None:
    match = _load_match(match)
    if match is None:
        return
    pairs = (
        (match.demand_request.user, match.supply_request.user),
        (match.supply_request.user, match.demand_request.user),
    )
    for recipient, other in pairs:
        _send_to_user(
            recipient.telegram_user_id,
            new_match_text(match),
            connected_markup(other, intro_draft_for_match(match, recipient)),
        )


def notify_match_accepted(match: Match) -> None:
    notify_connected(match)


def notify_connected(match: Match) -> None:
    match = _load_match(match)
    if match is None:
        return
    pairs = (
        (match.demand_request.user, match.supply_request.user),
        (match.supply_request.user, match.demand_request.user),
    )
    for recipient, other in pairs:
        prefix = match_accepted_text() if match.is_requester(recipient) else "Match accepted."
        text = f"{prefix}\n\n{connected_text(match, recipient)}"
        _send_to_user(
            recipient.telegram_user_id,
            text,
            connected_markup(other, intro_draft_for_match(match, recipient)),
        )


def notify_match_rejected(match: Match) -> None:
    match = _load_match(match)
    if match is None:
        return
    requester = match.requester_user()
    if requester:
        _send_to_user(requester.telegram_user_id, match_rejected_text(), open_koolbar_markup("matches"))
    owner = match.owner_request().user
    _send_to_user(owner.telegram_user_id, close_listing_prompt_text(), close_listing_markup(match.pk))


def notify_match_cancelled(match: Match) -> None:
    match = _load_match(match)
    if match is None:
        return
    owner = match.owner_request().user
    _send_to_user(owner.telegram_user_id, match_cancelled_text(), open_koolbar_markup("matches"))


def notify_support_reply(ticket: SupportTicket) -> None:
    ticket = _load_ticket(ticket)
    if ticket is None:
        return
    _send_to_user(
        ticket.user.telegram_user_id,
        support_reply_text(ticket),
        open_koolbar_markup(f"ticket_{ticket.pk}", "Open Support Ticket"),
    )


def notify_support_auto_closed(ticket: SupportTicket) -> None:
    ticket = _load_ticket(ticket)
    if ticket is None:
        return
    _send_to_user(
        ticket.user.telegram_user_id,
        support_auto_closed_text(),
        open_koolbar_markup(f"ticket_{ticket.pk}", "Open Support Ticket"),
    )


def _load_ticket(ticket: SupportTicket) -> SupportTicket | None:
    return SupportTicket.objects.select_related("user").filter(pk=ticket.pk).first()


def _load_match(match: Match) -> Match | None:
    return (
        Match.objects.select_related(
            "initiated_by",
            "demand_request",
            "demand_request__user",
            "supply_request",
            "supply_request__user",
        )
        .filter(pk=match.pk)
        .first()
    )


def _deliverable_chat_id(chat_id: int | None) -> int | None:
    try:
        value = int(chat_id or 0)
    except (TypeError, ValueError):
        return None
    if 0 < value < SYNTHETIC_TELEGRAM_USER_ID:
        return value
    return None


def _send_to_user(chat_id: int, text: str, markup: dict) -> None:
    deliverable = _deliverable_chat_id(chat_id)
    if deliverable is None:
        return
    try:
        send_telegram_message(deliverable, text, reply_markup=markup)
    except Exception:
        logger.exception("Failed to notify Telegram user %s", deliverable)
