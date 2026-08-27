from __future__ import annotations

from django.core.exceptions import ValidationError

from item_requests.models import RequestStatus
from matching.models import Match, MatchRating, MatchStatus
from matching.services import expire_open_matches_for_request
from users.models import User


def complete_match(match: Match, user: User) -> Match:
    _assert_participant(match, user)
    if match.status == MatchStatus.COMPLETED:
        return match
    if match.status != MatchStatus.CONNECTED:
        raise ValidationError({"status": "Only a connected match can be marked finished."})
    match.status = MatchStatus.COMPLETED
    match.save(update_fields=["status", "updated_at"])
    for item in (match.demand_request, match.supply_request):
        if item.status == RequestStatus.ACTIVE:
            item.status = RequestStatus.COMPLETED
            item.save(update_fields=["status", "updated_at"])
            expire_open_matches_for_request(item)
    return match


def rate_match(match: Match, user: User, score: int, comment: str = "") -> MatchRating:
    _assert_participant(match, user)
    if match.status != MatchStatus.COMPLETED:
        raise ValidationError({"status": "You can rate after the order is finished."})
    if MatchRating.objects.filter(match=match, rater=user).exists():
        raise ValidationError({"score": "You already rated this order."})
    try:
        score_value = int(score)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"score": "Choose a rating from 1 to 5."}) from exc
    if score_value < 1 or score_value > 5:
        raise ValidationError({"score": "Choose a rating from 1 to 5."})
    note = (comment or "").strip()
    if len(note) > 500:
        raise ValidationError({"comment": "Keep the comment under 500 characters."})
    return MatchRating.objects.create(match=match, rater=user, score=score_value, comment=note)


def rating_state(match: Match, user: User) -> dict:
    ratings = {row.rater_id: row for row in match.ratings.all()}
    other = match.counterpart_request(user)
    other_id = other.user_id if other is not None else None
    mine = ratings.get(user.id)
    theirs = ratings.get(other_id) if other_id else None
    return {
        "my_rating": mine.score if mine else None,
        "my_comment": mine.comment if mine else "",
        "their_rating": theirs.score if theirs else None,
        "their_comment": theirs.comment if theirs else "",
        "can_complete": match.status == MatchStatus.CONNECTED,
        "can_rate": match.status == MatchStatus.COMPLETED and user.id not in ratings,
    }


def _assert_participant(match: Match, user: User) -> None:
    if match.role_for(user) is None:
        raise ValidationError({"user": "You are not part of this match."})
