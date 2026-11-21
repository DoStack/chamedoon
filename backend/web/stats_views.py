"""GET /api/v1/public/stats/ and /api/v1/public/stats/<city>.csv: open aggregate data, no auth, cached one hour."""

from __future__ import annotations

import logging

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET

from web import stats

logger = logging.getLogger("web")

CACHE_HEADER = f"public, max-age={stats.CACHE_SECONDS}"


def _open(response: HttpResponse) -> HttpResponse:
    response["Cache-Control"] = CACHE_HEADER
    response["Access-Control-Allow-Origin"] = "*"
    response["X-License"] = "CC BY 4.0, Chamedoon (chamedoon.bigde.li)"
    return response


def _failed() -> JsonResponse:
    response = JsonResponse({"code": "server_error", "detail": "Statistics could not be built. Safe to retry."}, status=503)
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def public_stats(request: HttpRequest) -> HttpResponse:
    try:
        body = stats.cached()
    except Exception:  # noqa: BLE001 - an aggregate page must never take the API down with it
        logger.exception("public stats failed")
        return _failed()
    return _open(JsonResponse(body, json_dumps_params={"ensure_ascii": False}))


@require_GET
def public_stats_csv(request: HttpRequest, city: str) -> HttpResponse:
    try:
        body = stats.cached()
    except Exception:  # noqa: BLE001
        logger.exception("public stats csv failed")
        return _failed()
    text = stats.city_csv(body, city)
    if text is None:
        response = JsonResponse({"code": "not_found", "detail": "No such city."}, status=404)
        response["Cache-Control"] = "no-store"
        return response
    response = HttpResponse(text, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'inline; filename="{city}-rent-weekly.csv"'
    return _open(response)
