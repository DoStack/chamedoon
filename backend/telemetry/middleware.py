"""One `api_request` per write and per failure on the Mini App API. Reads that succeed stay out, they are most of the
traffic, the browser reports them and the free tier is finite."""

from __future__ import annotations

import time

from django.urls import resolve

from telemetry import capture, enabled, scrub_text

_WRITES = {"POST", "PUT", "PATCH", "DELETE"}


class ApiCaptureMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if not path.startswith("/api/v1/") or not enabled():
            return self.get_response(request)
        started = time.monotonic()
        response = self.get_response(request)
        try:
            self._capture(request, response, path, started)
        except Exception:  # noqa: BLE001 - telemetry never breaks a request
            pass
        return response

    def _capture(self, request, response, path: str, started: float) -> None:
        status = response.status_code
        if request.method not in _WRITES and status < 400:
            return
        try:
            route = resolve(path).url_name or "unknown"
        except Exception:  # noqa: BLE001
            route = "unknown"
        data = getattr(response, "data", None)
        code = data.get("code") if isinstance(data, dict) else None
        # DRF keeps the authenticated user on its own request object, which the response carries.
        drf_request = getattr(response, "renderer_context", {}).get("request") if hasattr(response, "renderer_context") else None
        user = getattr(drf_request, "user", None)
        capture(
            "api_request",
            telegram_id=getattr(user, "telegram_user_id", None),
            source="api",
            side="courier" if path.startswith("/api/v1/courier/") else "housing",
            properties={"route": route, "method": request.method, "status": status, "code": code, "duration_ms": round((time.monotonic() - started) * 1000), "path": scrub_text(path)},
        )
