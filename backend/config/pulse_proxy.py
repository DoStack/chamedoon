"""Forward /admin/pulse/ to the Pulse process on the Chamedoon VM (see docs/pulse.md).

Vercel's Django preset does not apply vercel.json rewrites, so this view passes the request through. It does no work
of its own: Pulse checks the admin session, runs every query and renders the page. GET and HEAD only.
"""

from __future__ import annotations

import os
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
