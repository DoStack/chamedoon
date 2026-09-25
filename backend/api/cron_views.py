from __future__ import annotations

from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.request import Request
from rest_framework.response import Response

from api.permissions import IsCronService
from item_requests.services import expire_due_requests
from support.services import auto_close_stale_tickets
from market.extract import convert_stored_posts, extract_channels_only, publish_stored_requests
from market.ingest import clamp_lookback_days, ingest_all_market_channels, market_ingest_enabled
from market.job import DAILY_LOOKBACK_DAYS
from market.migrate import migrate_market_posts
from outreach.services import build_outreach_queue, building_enabled


def _lookback_days(request: Request):
    raw = request.query_params.get("days") or request.data.get("days")
    if raw in {None, ""}:
        return DAILY_LOOKBACK_DAYS
    return clamp_lookback_days(raw)


def _cron_run_payload(run: dict, step: str, *, closed_tickets: int = 0) -> dict:
    return {
        "ok": bool(run.get("ok")),
        "step": step,
        "days": run.get("days"),
        "expired": run.get("expired", 0),
        "closed_tickets": closed_tickets,
        "ingest": run.get("ingest"),
        "migrate": run.get("migrate"),
        "publish": run.get("publish"),
        "result": run.get("result"),
    }


@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([IsCronService])
def ingest_market_channel_cron(_request: Request) -> Response:
    if not market_ingest_enabled():
        return Response({"ok": False, "error": "market ingest disabled"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    result = ingest_all_market_channels()
    code = status.HTTP_200_OK if result.get("ok") else status.HTTP_502_BAD_GATEWAY
    return Response(result, status=code)


@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([IsCronService])
def migrate_market_posts_cron(request: Request) -> Response:
    return market_extract_cron(request)


@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([IsCronService])
def market_extract_cron(request: Request) -> Response:
    closed_tickets = auto_close_stale_tickets()
    if not market_ingest_enabled():
        expired = expire_due_requests()
        return Response(
            {
                "ok": False,
                "step": "extract",
                "error": "market ingest disabled",
                "expired": expired,
                "closed_tickets": closed_tickets,
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    run = extract_channels_only(days=_lookback_days(request))
    return Response(_cron_run_payload(run, "extract", closed_tickets=closed_tickets), status=status.HTTP_200_OK)


@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([IsCronService])
def market_convert_cron(request: Request) -> Response:
    if not market_ingest_enabled():
        return Response(
            {"ok": False, "step": "convert", "error": "market ingest disabled"},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    run = convert_stored_posts(days=_lookback_days(request))
    return Response(_cron_run_payload(run, "convert"), status=status.HTTP_200_OK)


@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([IsCronService])
def market_publish_cron(_request: Request) -> Response:
    run = publish_stored_requests()
    return Response(_cron_run_payload(run, "publish"), status=status.HTTP_200_OK)


@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([IsCronService])
def expire_requests_cron(_request: Request) -> Response:
    expired = expire_due_requests()
    return Response({"ok": True, "expired": expired}, status=status.HTTP_200_OK)


@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([IsCronService])
def auto_close_tickets_cron(_request: Request) -> Response:
    closed = auto_close_stale_tickets()
    return Response({"ok": True, "closed": closed}, status=status.HTTP_200_OK)


@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([IsCronService])
def outreach_build_cron(_request: Request) -> Response:
    if not building_enabled():
        return Response(
            {"ok": False, "step": "outreach-build", "error": "outreach disabled"},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    result = build_outreach_queue()
    return Response({"ok": True, "step": "outreach-build", **result.as_dict()}, status=status.HTTP_200_OK)


@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([IsCronService])
def retry_market_llm_cron(_request: Request) -> Response:
    if not market_ingest_enabled():
        return Response({"ok": False, "error": "market ingest disabled"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    migrated = migrate_market_posts(pending_llm_only=True)
    return Response({"ok": True, "migrate": migrated}, status=status.HTTP_200_OK)
