from __future__ import annotations

import logging

from matching.contact import intro_draft_for_match
from matching.models import Match
from notifications.messages import (
    close_listing_prompt_text,
    connected_text,
    match_accepted_text,
    match_cancelled_text,
    match_rejected_text,
    new_match_text,
)
from notifications.telegram import (
    close_listing_markup,
    connected_markup,
    match_decision_markup,
    open_koolbar_markup,
    send_telegram_message,
)

logger = logging.getLogger(__name__)


def notify_new_match(match: Match) -> None:
    match = _load_match(match)
    if match is None:
        return
    owner = match.owner_request().user
    _send_to_user(owner.telegram_user_id, new_match_text(match), match_decision_markup(match.pk))


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


def _send_to_user(chat_id: int, text: str, markup: dict) -> None:
    try:
        send_telegram_message(chat_id, text, reply_markup=markup)
    except Exception:
        logger.exception("Failed to notify Telegram user %s", chat_id)
