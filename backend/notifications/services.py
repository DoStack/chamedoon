from __future__ import annotations

import logging

from matching.contact import intro_draft_for_match
from matching.models import Match, MatchStatus
from notifications.messages import connected_text, match_accepted_text, new_match_text
from notifications.telegram import connected_markup, open_koolbar_markup, send_telegram_message

logger = logging.getLogger(__name__)


def notify_new_match(match: Match) -> None:
    match = _load_match(match)
    if match is None:
        return
    text = new_match_text(match)
    markup = open_koolbar_markup("matches")
    _send_to_user(match.demand_request.user.telegram_user_id, text, markup)
    _send_to_user(match.supply_request.user.telegram_user_id, text, markup)


def notify_match_accepted(match: Match) -> None:
    match = _load_match(match)
    if match is None:
        return
    recipient_id = _waiting_party_chat_id(match)
    if recipient_id is None:
        return
    _send_to_user(recipient_id, match_accepted_text(), open_koolbar_markup("matches"))


def notify_connected(match: Match) -> None:
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
            connected_text(match, recipient),
            connected_markup(other, intro_draft_for_match(match, recipient)),
        )


def _load_match(match: Match) -> Match | None:
    return (
        Match.objects.select_related(
            "demand_request",
            "demand_request__user",
            "supply_request",
            "supply_request__user",
        )
        .filter(pk=match.pk)
        .first()
    )


def _waiting_party_chat_id(match: Match) -> int | None:
    if match.status == MatchStatus.ACCEPTED_BY_DEMAND:
        return match.supply_request.user.telegram_user_id
    if match.status == MatchStatus.ACCEPTED_BY_SUPPLY:
        return match.demand_request.user.telegram_user_id
    return None


def _send_to_user(chat_id: int, text: str, markup: dict) -> None:
    try:
        send_telegram_message(chat_id, text, reply_markup=markup)
    except Exception:
        logger.exception("Failed to notify Telegram user %s", chat_id)
