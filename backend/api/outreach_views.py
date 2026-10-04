"""Endpoints for the outreach worker (outreach_worker/) that holds the Telegram account."""

from __future__ import annotations

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.request import Request
from rest_framework.response import Response

from api.permissions import IsOutreachService
from matching.models import Match
from outreach.messages import posted_at_for
from outreach.models import OutreachMessage
from outreach.services import (
    OutreachConflict,
    build_outreach_queue,
    building_enabled,
    claim_next_message,
    hold_message,
    preview_messages,
    record_heartbeat,
    record_opt_out,
    record_reply,
    record_result,
    release_message,
    skip_reason,
    status_summary,
    valid_matches,
)


def _payload(request: Request) -> dict:
    return request.data if isinstance(request.data, dict) else {}


def _int_or_none(value) -> int | None:
    try:
        return int(value) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None


def _message_json(message: OutreachMessage) -> dict:
    return {"id": message.pk, "username": message.recipient_username, "text": message.text}


@api_view(["POST"])
@authentication_classes([])
@permission_classes([IsOutreachService])
def outreach_claim(_request: Request) -> Response:
    message, reason = claim_next_message()
    return Response({"message": _message_json(message) if message else None, "reason": reason})


@api_view(["POST"])
@authentication_classes([])
@permission_classes([IsOutreachService])
def outreach_result(request: Request, pk: int) -> Response:
    message = OutreachMessage.objects.filter(pk=pk).first()
    if message is None:
        return Response({"detail": "Unknown outreach message"}, status=status.HTTP_404_NOT_FOUND)
    data = _payload(request)
    try:
        record_result(
            message,
            outcome=str(data.get("outcome") or ""),
            error_code=str(data.get("error_code") or ""),
            error_detail=str(data.get("error_detail") or ""),
            recipient_telegram_id=_int_or_none(data.get("recipient_telegram_id")),
            telegram_message_id=_int_or_none(data.get("telegram_message_id")),
            retry_after_seconds=_int_or_none(data.get("retry_after_seconds")),
        )
    except OutreachConflict as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response({"id": message.pk, "status": message.status})


@api_view(["POST"])
@authentication_classes([])
@permission_classes([IsOutreachService])
def outreach_hold(request: Request, pk: int) -> Response:
    return _change_status(pk, hold_message, reason=str(_payload(request).get("reason") or ""))


@api_view(["POST"])
@authentication_classes([])
@permission_classes([IsOutreachService])
def outreach_release(_request: Request, pk: int) -> Response:
    return _change_status(pk, release_message)


def _change_status(pk: int, change, **kwargs) -> Response:
    message = OutreachMessage.objects.filter(pk=pk).first()
    if message is None:
        return Response({"detail": "Unknown outreach message"}, status=status.HTTP_404_NOT_FOUND)
    try:
        change(message, **kwargs)
    except OutreachConflict as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
    return Response({"id": message.pk, "status": message.status})


@api_view(["POST"])
@authentication_classes([])
@permission_classes([IsOutreachService])
def outreach_opt_out(request: Request) -> Response:
    data = _payload(request)
    opt_out = record_opt_out(
        username=str(data.get("username") or ""),
        telegram_user_id=_int_or_none(data.get("telegram_user_id")),
    )
    if opt_out is None:
        return Response({"detail": "username or telegram_user_id is required"}, status=status.HTTP_400_BAD_REQUEST)
    return Response({"ok": True})


@api_view(["POST"])
@authentication_classes([])
@permission_classes([IsOutreachService])
def outreach_reply(request: Request) -> Response:
    data = _payload(request)
    message = record_reply(
        username=str(data.get("username") or ""),
        telegram_user_id=_int_or_none(data.get("telegram_user_id")),
    )
    return Response({"ok": True, "message_id": message.pk if message else None})


@api_view(["POST"])
@authentication_classes([])
@permission_classes([IsOutreachService])
def outreach_heartbeat(_request: Request) -> Response:
    record_heartbeat()
    return Response(status_summary())


@api_view(["POST"])
@authentication_classes([])
@permission_classes([IsOutreachService])
def outreach_build(_request: Request) -> Response:
    """Same as the daily cron, for when the worker should not wait for it."""
    if not building_enabled():
        return Response({"ok": False, "error": "outreach disabled"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    return Response({"ok": True, **build_outreach_queue().as_dict()})


@api_view(["GET"])
@authentication_classes([])
@permission_classes([IsOutreachService])
def outreach_preview(request: Request) -> Response:
    limit = _int_or_none(request.query_params.get("limit")) or 5
    now = timezone.now()
    return Response(
        {
            **status_summary(now),
            "messages": [_preview_json(message, now) for message in preview_messages(limit, now)],
        }
    )


def _preview_json(message: OutreachMessage, now) -> dict:
    """The message plus what an operator needs to judge it: travelers, source post, skip reason."""
    demand = message.demand_request
    return {
        **_message_json(message),
        "skip": skip_reason(message, now),
        "source_url": demand.source_url,
        "posted_at": posted_at_for(demand).isoformat(),
        "travelers": [_traveler_json(match) for match in valid_matches(demand, now)],
    }


def _traveler_json(match: Match) -> dict:
    supply = match.supply_request
    return {
        "request_id": supply.pk,
        "username": supply.user.telegram_username or "",
        "flight_date": supply.flight_date.isoformat() if supply.flight_date else None,
        "date_from": supply.date_from.isoformat(),
        "date_to": supply.date_to.isoformat(),
        "capacity_kg": str(supply.capacity_kg) if supply.capacity_kg is not None else None,
        "score": str(match.score),
        "imported": supply.imported,
    }
