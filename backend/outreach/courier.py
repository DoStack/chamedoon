"""Courier models beyond C1 (phase 3): C2 to a traveler whose trip we imported, C3 the Nowruz wave in February and March.

Same rules as every first message: a person, a launch city at one end of the route, posted in the last days, one
message ever. The link opens the courier Mini App on their own imported trip or request, which becomes theirs on sign-in.
"""

from __future__ import annotations

import random
from datetime import datetime

from django.db.models import Q
from django.utils import timezone

from item_requests.models import ItemRequest, RequestStatus, RequestType
from matching.contact import SYNTHETIC_TELEGRAM_USER_ID, city_fa, clean_telegram_username, is_shadow_user
from matching.rules import is_person_request
from notifications.telegram import mini_app_in_chat_link
from outreach import conf, templates
from outreach.messages import posted_at_for
from outreach.models import OutreachKind, OutreachMessage, OutreachStatus

NOWRUZ_MONTHS = {2, 3}


def in_nowruz_season(now: datetime) -> bool:
    return now.month in NOWRUZ_MONTHS


def _imported(kind: str, now: datetime):
    cutoff = now - conf.max_post_age()
    return (
        ItemRequest.objects.filter(
            type=kind,
            status=RequestStatus.ACTIVE,
            imported=True,
            expires_at__gt=now,
            user__is_active=True,
            user__telegram_user_id__gte=SYNTHETIC_TELEGRAM_USER_ID,
        )
        .filter(Q(market_post__posted_at__gte=cutoff) | Q(market_post__isnull=True, created_at__gte=cutoff))
        .select_related("user", "market_post")
        .order_by("-created_at")
    )


def _route_names(request: ItemRequest) -> tuple[str, str]:
    return city_fa(request.origin_country, request.origin_city), city_fa(request.destination_country, request.destination_city)


def build_courier_waves(result, seen: set[str], *, now: datetime, dry_run: bool) -> None:
    from outreach.services import _recipient_skip_reason, courier_metro, new_message_status, new_token

    kinds = conf.enabled_kinds()
    waves = []
    if OutreachKind.C2 in kinds:
        waves.append((OutreachKind.C2, _imported(RequestType.SUPPLY, now)))
    if OutreachKind.C3 in kinds and in_nowruz_season(now):
        waves.append((OutreachKind.C3, _imported(RequestType.DEMAND, now).filter(outreach_message__isnull=True)))
    for kind, requests in waves:
        for request in requests:
            username = clean_telegram_username(request.user.telegram_username)
            reason = _recipient_skip_reason(username, now, seen) or ("not_a_person" if not is_person_request(request) else "")
            if reason:
                result.skip(reason)
                continue
            place = courier_metro(request)
            if place is None:
                result.skip("outside_cities")
                continue
            seen.add(username.lower())
            status = new_message_status()
            message = OutreachMessage(
                kind=kind,
                recipient=request.user,
                recipient_username=username,
                recipient_name=request.user.first_name or "",
                courier_request=request,
                country=place.country,
                city=place.city,
                source_at=posted_at_for(request),
                token=new_token(),
                text="",
                status=status,
                error_code="awaiting_yes" if status == OutreachStatus.HELD else "",
            )
            message.text = render(message)
            if not dry_run:
                message.save()
            result.created.append(message)


def render(message: OutreachMessage) -> str:
    request = message.courier_request
    if request is None:
        return message.text
    rng = random.Random(message.token)
    name = templates.valid_name(message.recipient_name, message.recipient_username)
    origin, destination = _route_names(request)
    url = mini_app_in_chat_link(f"o_{message.token}")
    if message.kind == OutreachKind.C2:
        text, variant = templates.c2(rng, name, origin, destination, url)
    else:
        text, variant = templates.c3(rng, name, origin, destination, url)
    message.variant = variant
    return text


def skip_reason(message: OutreachMessage, now: datetime) -> str:
    from outreach.services import already_contacted, courier_metro, is_opted_out

    request = message.courier_request
    if request is None or request.status != RequestStatus.ACTIVE or request.expires_at <= now:
        return "request_closed"
    if message.recipient is None or not message.recipient.is_active or not is_shadow_user(message.recipient):
        return "claimed"
    if posted_at_for(request) < now - conf.max_post_age():
        return "stale"
    if message.kind == OutreachKind.C3 and not in_nowruz_season(now):
        return "season_over"
    if courier_metro(request) is None:
        return "outside_cities"
    if is_opted_out(username=message.recipient_username):
        return "opted_out"
    if already_contacted(username=message.recipient_username, exclude_pk=message.pk):
        return "contacted"
    return ""
