"""GET /api/v1/public/stats/: aggregate numbers for the public site's build only.

The site bakes these numbers into its pages at build time; the raw JSON is not published. The build sends the shared
secret `settings.PUBLIC_STATS_TOKEN` in the `X-Stats-Token` header. No token configured = closed for everyone.
"""

from __future__ import annotations

import hmac
import logging

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET

from web import stats

logger = logging.getLogger("web")

HEADER = "HTTP_X_STATS_TOKEN"


def _private(response: HttpResponse) -> HttpResponse:
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _allowed(request: HttpRequest) -> bool:
    expected = getattr(settings, "PUBLIC_STATS_TOKEN", "")
    given = request.META.get(HEADER, "")
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


@require_GET
def public_stats(request: HttpRequest) -> HttpResponse:
    if not _allowed(request):
        return _private(JsonResponse({"code": "forbidden", "detail": "Not available."}, status=403))
    try:
        body = stats.cached()
    except Exception:  # noqa: BLE001 - an aggregate page must never take the API down with it
        logger.exception("public stats failed")
        return _private(JsonResponse({"code": "server_error", "detail": "Statistics could not be built. Safe to retry."}, status=503))
    return _private(JsonResponse(body, json_dumps_params={"ensure_ascii": False}))
