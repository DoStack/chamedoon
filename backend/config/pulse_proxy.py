"""Forward /admin/pulse/ to the Pulse process on the Chamedoon VM (see docs/pulse.md).

Vercel's Django preset does not apply vercel.json rewrites, so this view passes the request through. It does no work
of its own: Pulse checks the admin session, runs every query and renders the page. GET and HEAD only.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
import urllib.error
import urllib.request

from django.http import HttpResponse, HttpResponseNotAllowed

TIMEOUT = 60
PASS_REQUEST = ("Cookie", "User-Agent", "Accept", "Accept-Language", "If-None-Match", "If-Modified-Since")
PASS_RESPONSE = ("Content-Type", "Cache-Control", "ETag", "Last-Modified", "Location", "Expires", "Vary")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


MAX_SKEW = 120  # seconds a signed pass stays valid


def _secret() -> bytes:
    return os.environ.get("PULSE_PROXY_SECRET", "").encode()


def sign(user_id: int, now: int | None = None) -> tuple[str, str]:
    """(timestamp, signature) the main site sends so Pulse can trust the admin login it already checked."""
    ts = str(int(time.time()) if now is None else now)
    return ts, hmac.new(_secret(), f"{user_id}:{ts}".encode(), hashlib.sha256).hexdigest()


def verify(user_id: str, ts: str, signature: str, now: int | None = None) -> bool:
    if not _secret() or not user_id.isdigit() or not ts.isdigit() or not signature:
        return False
    if abs((int(time.time()) if now is None else now) - int(ts)) > MAX_SKEW:
        return False
    expected = hmac.new(_secret(), f"{user_id}:{ts}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def origin() -> str:
    return os.environ.get("PULSE_ORIGIN", "https://chamedoon-pulse.bigde.li").rstrip("/")


def pulse_proxy(request, rest: str = ""):
    if request.method not in {"GET", "HEAD"}:
        return HttpResponseNotAllowed(["GET", "HEAD"])
    url = f"{origin()}/admin/pulse/{rest}"
    if request.META.get("QUERY_STRING"):
        url += "?" + request.META["QUERY_STRING"]
    headers = {name: request.headers[name] for name in PASS_REQUEST if name in request.headers}
    headers["X-Forwarded-Proto"] = "https"
    # The two sites sign sessions with different keys, so the main site vouches for the admin it already checked.
    user = getattr(request, "user", None)
    if _secret() and user is not None and user.is_authenticated and user.is_active and user.is_staff:
        headers["X-Pulse-User"] = str(user.pk)
        headers["X-Pulse-Ts"], headers["X-Pulse-Sig"] = sign(user.pk)
    upstream = urllib.request.Request(url, headers=headers, method=request.method)
    try:
        response = _opener.open(upstream, timeout=TIMEOUT)
    except urllib.error.HTTPError as error:  # 3xx (not followed), 4xx and 5xx all come back as HTTPError
        response = error
    except (urllib.error.URLError, TimeoutError):
        return HttpResponse("Pulse is not reachable right now. It runs on the Chamedoon VM.", status=502, content_type="text/plain")
    with response:
        body = response.read() if request.method == "GET" else b""
        out = HttpResponse(body, status=response.status)
        for name in PASS_RESPONSE:
            value = response.headers.get(name)
            if value:
                out[name] = value.replace(origin(), "") if name == "Location" else value
    return out
