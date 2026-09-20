from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from item_requests.models import ItemRequest, RequestStatus, RequestType
from matching.models import (
    MIN_VISIBLE_SCORE,
    OPEN_MATCH_STATUSES,
    UNFINISHED_MATCH_STATUSES,
    Match,
    MatchStatus,
)
from matching.rules import pair_demand_supply, passes_hard_rules
from matching.scoring import calculate_score

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MatchCandidate:
    demand: ItemRequest
    supply: ItemRequest
    score: Decimal


def find_match_candidates(item_request: ItemRequest) -> list[MatchCandidate]:
    from item_requests.services import expire_due_requests

    expire_due_requests(sync_channel=False)
    item_request.refresh_from_db()
    if item_request.status != RequestStatus.ACTIVE or item_request.is_expired():
        return []

    candidates: list[MatchCandidate] = []
    for opposite in _opposite_candidates(item_request):
        demand, supply = pair_demand_supply(item_request, opposite)
        if not passes_hard_rules(demand, supply):
            continue
        score = calculate_score(demand, supply)
        if score < MIN_VISIBLE_SCORE:
            continue
        candidates.append(MatchCandidate(demand=demand, supply=supply, score=score))
    return candidates


def persist_candidates(
    candidates: list[MatchCandidate],
    *,
    initiated_by=None,
) -> list[Match]:
    stored: list[Match] = []
    for candidate in candidates:
        match = _upsert_suggested_match(candidate, initiated_by=initiated_by)
        if match is not None:
            stored.append(match)
    return stored


def find_matches(item_request: ItemRequest) -> list[Match]:
    return persist_candidates(
        find_match_candidates(item_request),
        initiated_by=item_request.user,
    )


def sync_matches_for_request(item_request: ItemRequest) -> list[Match]:
    if item_request.status != RequestStatus.ACTIVE or item_request.is_expired():
        expire_unfinished_matches_for_request(item_request)
        return []
    _drop_invalid_open_matches(item_request)
    return find_matches(item_request)


def expire_open_matches_for_request(item_request: ItemRequest) -> int:
    return _expire_matches_for_request(item_request, OPEN_MATCH_STATUSES)


def expire_unfinished_matches_for_request(item_request: ItemRequest) -> int:
    return _expire_matches_for_request(item_request, UNFINISHED_MATCH_STATUSES)


def expire_open_matches_for_request_ids(request_ids: list[int]) -> int:
    return _expire_matches_for_request_ids(request_ids, OPEN_MATCH_STATUSES)


def expire_unfinished_matches_for_request_ids(request_ids: list[int]) -> int:
    return _expire_matches_for_request_ids(request_ids, UNFINISHED_MATCH_STATUSES)


def _expire_matches_for_request(item_request: ItemRequest, statuses: tuple[str, ...]) -> int:
    now = timezone.now()
    return Match.objects.filter(
        _matches_for_request(item_request),
        status__in=statuses,
    ).update(status=MatchStatus.EXPIRED, updated_at=now)


def _expire_matches_for_request_ids(request_ids: list[int], statuses: tuple[str, ...]) -> int:
    if not request_ids:
        return 0
    now = timezone.now()
    return Match.objects.filter(
        Q(demand_request_id__in=request_ids) | Q(supply_request_id__in=request_ids),
        status__in=statuses,
    ).update(status=MatchStatus.EXPIRED, updated_at=now)


def _opposite_candidates(item_request: ItemRequest):
    opposite_type = (
        RequestType.SUPPLY if item_request.type == RequestType.DEMAND else RequestType.DEMAND
    )
    now = timezone.now()
    return (
        ItemRequest.objects.filter(
            type=opposite_type,
            status=RequestStatus.ACTIVE,
            expires_at__gt=now,
            origin_country=item_request.origin_country,
            origin_city=item_request.origin_city,
        )
        .exclude(user_id=item_request.user_id)
        .exclude(pk=item_request.pk)
        .prefetch_related("item_categories", "excluded_categories")
        .order_by("-created_at")[:50]
    )


def _matches_for_request(item_request: ItemRequest):
    return Q(demand_request=item_request) | Q(supply_request=item_request)


def _drop_invalid_open_matches(item_request: ItemRequest) -> None:
    open_matches = (
        Match.objects.filter(_matches_for_request(item_request), status__in=OPEN_MATCH_STATUSES)
        .select_related("demand_request", "supply_request")
        .prefetch_related(
            "demand_request__item_categories",
            "supply_request__item_categories",
            "supply_request__excluded_categories",
        )
    )
    for match in open_matches:
        demand = match.demand_request
        supply = match.supply_request
        still_valid = passes_hard_rules(demand, supply) and calculate_score(demand, supply) >= MIN_VISIBLE_SCORE
        if not still_valid:
            match.status = MatchStatus.EXPIRED
            match.save(update_fields=["status", "updated_at"])


def _upsert_suggested_match(candidate: MatchCandidate, *, initiated_by=None) -> Match | None:
    existing = Match.objects.filter(
        demand_request=candidate.demand,
        supply_request=candidate.supply,
    ).first()
    if existing is None:
        match = Match.objects.create(
            demand_request=candidate.demand,
            supply_request=candidate.supply,
            initiated_by=initiated_by or candidate.demand.user,
            score=candidate.score,
            status=MatchStatus.CONNECTED,
        )
        _schedule_connected_notification(match.id)
        return match
    if existing.status in {
        MatchStatus.CONNECTED,
        MatchStatus.COMPLETED,
        MatchStatus.EXPIRED,
    }:
        return None
    if existing.score != candidate.score:
        existing.score = candidate.score
        existing.save(update_fields=["score", "updated_at"])
    return existing


def _schedule_connected_notification(match_id: int) -> None:
    def _send() -> None:
        try:
            from notifications.services import notify_connected

            match = (
                Match.objects.select_related("demand_request", "supply_request")
                .filter(pk=match_id)
                .first()
            )
            if match is None:
                return
            notify_connected(match)
        except Exception:
            logger.exception("Failed to notify connected match %s", match_id)

    transaction.on_commit(_send)
