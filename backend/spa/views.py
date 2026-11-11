import logging
from pathlib import Path

from django.http import HttpResponse, HttpResponsePermanentRedirect
from django.views.decorators.http import require_safe


SHELL_INDEX = Path(__file__).resolve().parent / "shell" / "index.html"

logger = logging.getLogger(__name__)


@require_safe
def shell(request):
    """The app for everyone (owner, 2026-11-11): the miniapp_v2 and courier_v2 flags no longer pick the old pages."""
    if not SHELL_INDEX.exists():
        return HttpResponse("frontend build missing", status=503, content_type="text/plain")
    response = HttpResponse(SHELL_INDEX.read_text(encoding="utf-8"), content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "no-cache"
    return response


def moved(target: str):
    """301 from an old address to the app's path for the same screen, keeping the query (Telegram's launch data)."""

    @require_safe
    def view(request, **parts):
        location = target.format(**parts)
        query = request.META.get("QUERY_STRING", "")
        return HttpResponsePermanentRedirect(location + (f"?{query}" if query else ""))

    return view
