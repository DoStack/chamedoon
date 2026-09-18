from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.db import transaction

from item_requests.models import RequestStatus
from matching.models import Match, MatchStatus
from matching.rules import is_matchable
from users.models import User

logger = logging.getLogger(__name__)


def accept_match(match: Match, user: User) -> Match:
    _assert_participant(match, user)
    _assert_requests_still_matchable(match)
    if match.status in {MatchStatus.EXPIRED, MatchStatus.COMPLETED}:
        raise ValidationError({"status": "This match can no longer be accepted."})
    if match.status == MatchStatus.CONNECTED:
        return match
    match.status = MatchStatus.CONNECTED
    match.save(update_fields=["status", "updated_at"])
    _notify_accepted(match)
    return match


def reject_match(match: Match, user: User) -> Match:
    _assert_participant(match, user)
    if match.status == MatchStatus.COMPLETED:
        raise ValidationError({"status": "A completed match cannot be rejected."})
    if match.status == MatchStatus.EXPIRED:
        return match
    if not match.is_owner(user):
        raise ValidationError({"status": "Only the listing owner can reject this match."})
    match.status = MatchStatus.EXPIRED
    match.save(update_fields=["status", "updated_at"])
    _notify_rejected(match)
    return match


def cancel_match(match: Match, user: User) -> Match:
    _assert_participant(match, user)
    raise ValidationError({"status": "Connected matches cannot be cancelled."})


def close_listing_after_reject(match: Match, user: User) -> None:
    _assert_participant(match, user)
    if not match.is_owner(user):
        raise ValidationError({"status": "Only the listing owner can close this listing."})
    listing = match.owner_request()
    if listing.status != RequestStatus.ACTIVE:
        return
    listing.status = RequestStatus.CLOSED
    listing.save(update_fields=["status", "updated_at"])
    from matching.services import expire_open_matches_for_request
    from item_requests.services import schedule_channel_sync

    expire_open_matches_for_request(listing)
    schedule_channel_sync(listing)


def keep_listing_after_reject(match: Match, user: User) -> None:
    _assert_participant(match, user)
    if not match.is_owner(user):
        raise ValidationError({"status": "Only the listing owner can keep this listing."})


def _assert_participant(match: Match, user: User) -> None:
    if match.role_for(user) is None:
        raise ValidationError({"user": "You are not part of this match."})


def _assert_requests_still_matchable(match: Match) -> None:
    if not is_matchable(match.demand_request) or not is_matchable(match.supply_request):
        raise ValidationError({"status": "One of the requests is no longer active."})


def _notify_accepted(match: Match) -> None:
    match_id = match.id

    def _send() -> None:
        try:
            from notifications.services import notify_connected

            current = Match.objects.filter(pk=match_id).first()
            if current is None:
                return
            notify_connected(current)
        except Exception:
            logger.exception("Failed to notify match acceptance %s", match_id)

    transaction.on_commit(_send)


def _notify_rejected(match: Match) -> None:
    match_id = match.id

    def _send() -> None:
        try:
            from notifications.services import notify_match_rejected

            current = Match.objects.filter(pk=match_id).first()
            if current is None:
                return
            notify_match_rejected(current)
        except Exception:
            logger.exception("Failed to notify match rejection %s", match_id)

    transaction.on_commit(_send)
