from __future__ import annotations

from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.request import Request
from rest_framework.response import Response

from api.permissions import IsCronService
from item_requests.services import expire_due_requests
from support.services import auto_close_stale_tickets
from market.ingest import ingest_all_market_channels, market_ingest_enabled
from market.migrate import migrate_market_posts


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
def migrate_market_posts_cron(_request: Request) -> Response:
    expired = expire_due_requests()
    closed_tickets = auto_close_stale_tickets()
    if not market_ingest_enabled():
        return Response(
            {
                "ok": False,
                "error": "market ingest disabled",
                "expired": expired,
                "closed_tickets": closed_tickets,
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    ingest = ingest_all_market_channels()
    migrated = migrate_market_posts()
    ok = bool(ingest.get("ok") and migrated.get("ok"))
    return Response(
        {
            "ok": ok,
            "expired": expired,
            "closed_tickets": closed_tickets,
            "ingest": ingest,
            "migrate": migrated,
        },
        status=status.HTTP_200_OK,
    )


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
def retry_market_llm_cron(_request: Request) -> Response:
    if not market_ingest_enabled():
        return Response({"ok": False, "error": "market ingest disabled"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    migrated = migrate_market_posts(pending_llm_only=True)
    return Response({"ok": True, "migrate": migrated}, status=status.HTTP_200_OK)
