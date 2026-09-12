from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction

from item_requests.models import ItemRequest, RequestStatus, RequestType
from matching.models import VISIBLE_MATCH_STATUSES, Match, MatchStatus
from matching.rules import pair_demand_supply, passes_hard_rules
from matching.scoring import calculate_score
from users.models import User


def validate_manual_pair(
    demand: ItemRequest,
    supply: ItemRequest,
    *,
    override_rules: bool = False,
) -> tuple[ItemRequest, ItemRequest]:
    try:
        demand, supply = pair_demand_supply(demand, supply)
    except ValueError as exc:
        raise ValidationError({"requests": str(exc)}) from exc
    if demand.type != RequestType.DEMAND or supply.type != RequestType.SUPPLY:
        raise ValidationError({"requests": "Manual match needs one DEMAND and one SUPPLY."})
    if demand.user_id == supply.user_id:
        raise ValidationError({"requests": "Cannot match two requests from the same user."})
    if demand.status != RequestStatus.ACTIVE or supply.status != RequestStatus.ACTIVE:
        raise ValidationError({"status": "Both requests must be ACTIVE."})
    if not override_rules and not passes_hard_rules(demand, supply):
        raise ValidationError(
            {
                "requests": (
                    "These requests do not pass matching rules. "
                    "Enable override to force the match."
                )
            }
        )
    if Match.objects.filter(demand_request=demand, supply_request=supply).exists():
        existing = Match.objects.get(demand_request=demand, supply_request=supply)
        raise ValidationError({"match": f"Match {existing.pk} already exists ({existing.status})."})
    return demand, supply


@transaction.atomic
def create_manual_match(
    demand: ItemRequest,
    supply: ItemRequest,
    *,
    status: str = MatchStatus.ACCEPTED,
    override_rules: bool = False,
    initiated_by: User | None = None,
) -> Match:
    demand, supply = validate_manual_pair(demand, supply, override_rules=override_rules)
    if status not in MatchStatus.values:
        raise ValidationError({"status": "Unknown match status."})
    score = calculate_score(demand, supply)
    match = Match.objects.create(
        demand_request=demand,
        supply_request=supply,
        initiated_by=initiated_by or demand.user,
        score=score,
        status=status,
    )
    _notify_manual_match(match)
    return match


@transaction.atomic
def set_match_status(match: Match, status: str) -> Match:
    if status not in MatchStatus.values:
        raise ValidationError({"status": "Unknown match status."})
    previous = match.status
    if previous == status:
        return match
    match.status = status
    match.save(update_fields=["status", "updated_at"])
    if status == MatchStatus.ACCEPTED and previous != MatchStatus.ACCEPTED:
        from notifications.services import notify_connected

        notify_connected(match)
    return match


def _notify_manual_match(match: Match) -> None:
    from django.db import transaction as db_transaction
    from notifications.services import notify_connected, notify_new_match

    match_id = match.id
    connected = match.status == MatchStatus.ACCEPTED

    def _send() -> None:
        current = Match.objects.filter(pk=match_id).first()
        if current is None:
            return
        if connected:
            notify_connected(current)
        else:
            notify_new_match(current)

    db_transaction.on_commit(_send)


def propose_user_match(user: User, other: ItemRequest, mine: ItemRequest | None = None) -> Match:
    if other.user_id == user.id:
        raise ValidationError({"request": "You cannot match with your own request."})
    if other.status != RequestStatus.ACTIVE or other.is_expired():
        raise ValidationError({"status": "This request is no longer active."})

    if mine is None:
        opposite = RequestType.SUPPLY if other.type == RequestType.DEMAND else RequestType.DEMAND
        candidates = list(
            ItemRequest.objects.filter(user=user, type=opposite, status=RequestStatus.ACTIVE)
        )
        candidates = [item for item in candidates if not item.is_expired()]
        if not candidates:
            raise ValidationError(
                {
                    "my_request_id": (
                        "Create an opposite request first "
                        "(send request to match a traveler, or traveler request to match a sender)."
                    )
                }
            )
        if len(candidates) > 1:
            raise ValidationError(
                {"my_request_id": "You have more than one opposite request. Choose which one to use."}
            )
        mine = candidates[0]
    elif mine.user_id != user.id:
        raise ValidationError({"my_request_id": "That request does not belong to you."})
    elif mine.status != RequestStatus.ACTIVE or mine.is_expired():
        raise ValidationError({"my_request_id": "Your request is not active."})
    elif mine.type == other.type:
        raise ValidationError({"my_request_id": "You need the opposite request type."})

    demand, supply = pair_demand_supply(mine, other)
    existing = Match.objects.filter(demand_request=demand, supply_request=supply).first()
    if existing is not None:
        if existing.status in VISIBLE_MATCH_STATUSES:
            return existing
        raise ValidationError({"match": "A match for this pair already exists and is no longer open."})
    return create_manual_match(demand, supply, override_rules=True, initiated_by=user)
