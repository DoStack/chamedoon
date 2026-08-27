from __future__ import annotations

from django.core.exceptions import ValidationError

from matching.models import Match, MatchStatus
from matching.rules import is_matchable
from users.models import User


def accept_match(match: Match, user: User) -> Match:
    _assert_participant(match, user)
    _assert_requests_still_matchable(match)
    if match.status in {MatchStatus.REJECTED, MatchStatus.EXPIRED, MatchStatus.COMPLETED}:
        raise ValidationError({"status": "This match can no longer be accepted."})
    if match.status == MatchStatus.CONNECTED:
        return match

    role = match.role_for(user)
    if role == "demand":
        if match.status == MatchStatus.ACCEPTED_BY_DEMAND:
            return match
        match.status = (
            MatchStatus.CONNECTED
            if match.status == MatchStatus.ACCEPTED_BY_SUPPLY
            else MatchStatus.ACCEPTED_BY_DEMAND
        )
    else:
        if match.status == MatchStatus.ACCEPTED_BY_SUPPLY:
            return match
        match.status = (
            MatchStatus.CONNECTED
            if match.status == MatchStatus.ACCEPTED_BY_DEMAND
            else MatchStatus.ACCEPTED_BY_SUPPLY
        )
    match.save(update_fields=["status", "updated_at"])
    _notify_acceptance(match)
    return match


def reject_match(match: Match, user: User) -> Match:
    _assert_participant(match, user)
    if match.status in {MatchStatus.CONNECTED, MatchStatus.COMPLETED}:
        raise ValidationError({"status": "A connected match cannot be rejected."})
    if match.status == MatchStatus.REJECTED:
        return match
    match.status = MatchStatus.REJECTED
    match.save(update_fields=["status", "updated_at"])
    return match


def _assert_participant(match: Match, user: User) -> None:
    if match.role_for(user) is None:
        raise ValidationError({"user": "You are not part of this match."})


def _assert_requests_still_matchable(match: Match) -> None:
    if not is_matchable(match.demand_request) or not is_matchable(match.supply_request):
        raise ValidationError({"status": "One of the requests is no longer active."})


def _notify_acceptance(match: Match) -> None:
    from django.db import transaction
    from notifications.services import notify_connected, notify_match_accepted

    match_id = match.id
    connected = match.status == MatchStatus.CONNECTED

    def _send() -> None:
        current = Match.objects.filter(pk=match_id).first()
        if current is None:
            return
        if connected:
            notify_connected(current)
        else:
            notify_match_accepted(current)

    transaction.on_commit(_send)
