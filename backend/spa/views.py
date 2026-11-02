import os
from pathlib import Path

from django.http import Http404, HttpResponse
from django.views.decorators.http import require_safe

SHELL_INDEX = Path(__file__).resolve().parent / "shell" / "index.html"


def _shell_enabled():
    """Fails closed: on Vercel only explicit non-production environments get the shell."""
    if os.environ.get("VERCEL"):
        return os.environ.get("VERCEL_ENV") in {"preview", "development"}
    return True


@require_safe
def shell(request):
    if not _shell_enabled():
        raise Http404
    if not SHELL_INDEX.exists():
        return HttpResponse("frontend build missing", status=503, content_type="text/plain")
    response = HttpResponse(SHELL_INDEX.read_text(encoding="utf-8"), content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "no-cache"
    return response
