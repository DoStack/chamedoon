# SPIKE P0: throwaway, delete before P1. No view here touches the database.
import time
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse, JsonResponse

BUILD_DIR = Path(__file__).resolve().parent / "static" / "spike_p0"


def shell(request, path=""):
    index = BUILD_DIR / "index.html"
    if not index.exists():
        return HttpResponse("spike build output missing", status=503, content_type="text/plain")
    return HttpResponse(index.read_text(encoding="utf-8"), content_type="text/html; charset=utf-8")


def cached(request):
    """Static JSON for the CDN test. served_at only changes when the function really ran."""
    response = JsonResponse({"spike": "p0", "static": True, "served_at": time.time()})
    response["Cache-Control"] = "public, max-age=0, s-maxage=600"
    return response


def diag(request):
    """Where the build output landed. Paths and counts only, never env values."""
    static_root = Path(settings.STATIC_ROOT)
    response = JsonResponse(
        {
            "debug": settings.DEBUG,
            "storage": settings.STORAGES["staticfiles"]["BACKEND"],
            "build_index_in_function": (BUILD_DIR / "index.html").exists(),
            "build_files_in_function": sum(1 for p in BUILD_DIR.rglob("*") if p.is_file()) if BUILD_DIR.exists() else 0,
            "static_root_in_function": static_root.exists(),
            "collected_spike_in_function": (static_root / "spike_p0" / "index.html").exists(),
            "frontend_spike_in_function": (Path(settings.BASE_DIR).parent / "frontend-spike").exists(),
        }
    )
    response["Cache-Control"] = "no-store"
    return response
