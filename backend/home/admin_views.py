from __future__ import annotations

import json

from django.contrib import admin
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from home import conf
from home.job import STEPS, run_home_job
from home.models import HomeSource
from home.status import home_status


def _int(value, low: int, high: int) -> int | None:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return None


@require_http_methods(["GET", "POST"])
def home_run_view(request):
    run = None
    selected = (request.POST.get("source") or "").strip()
    days = _int(request.POST.get("days"), 1, conf.MAX_LOOKBACK_DAYS) or conf.lookback_days()
    limit = _int(request.POST.get("limit"), 1, conf.MAX_REVIEW_BATCH)
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action in STEPS:
            run = run_home_job(
                step=action,
                days=days,
                review_limit=limit,
                media_limit=min(limit, conf.MAX_MEDIA_BATCH) if limit else None,
                usernames=[selected] if selected and action == "ingest" else None,
            )
        else:
            run = {"ok": False, "error": "Unknown action"}
    context = {
        **admin.site.each_context(request),
        "title": "Chamedoon Home",
        "subtitle": "Read the housing groups, let the AI review the posts, store the photos",
        "status": home_status(),
        "run": run,
        "run_json": json.dumps(run, ensure_ascii=False, indent=2, default=str) if run is not None else "",
        "sources": list(HomeSource.objects.values_list("username", flat=True)),
        "selected": selected,
        "days": days,
        "limit": limit or "",
    }
    return render(request, "admin/home_run.html", context)
