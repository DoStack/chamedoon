import logging
import os
from pathlib import Path

from django.http import Http404, HttpResponse
from django.views.decorators.http import require_safe

from home import conf

SHELL_INDEX = Path(__file__).resolve().parent / "shell" / "index.html"

logger = logging.getLogger(__name__)


def _shell_enabled():
    """Fails closed. This request has no identity, so on Production it only asks whether anyone can have the new app;
    the bundle then asks /public/meta/ and the exchange about this person, and sends everyone else to the old pages."""
    if not os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV") in {"preview", "development"}:
        return True
    if os.environ.get("VERCEL_ENV") != "production":
        return False
    try:
        row = conf.row()
    except Exception:
        logger.exception("spa_shell_flag_failed")
        return False
    return bool(row.miniapp_v2 or (row.miniapp_v2_allowlist or "").strip())


@require_safe
def shell(request):
    if not _shell_enabled():
        raise Http404
    if not SHELL_INDEX.exists():
        return HttpResponse("frontend build missing", status=503, content_type="text/plain")
    response = HttpResponse(SHELL_INDEX.read_text(encoding="utf-8"), content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "no-cache"
    return response
