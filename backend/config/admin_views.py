from __future__ import annotations

from django.contrib import admin
from django.shortcuts import redirect, render
from django.urls import path
from django.views.decorators.http import require_http_methods

from market.extract import extract_channel, extract_one_post, extract_status
from market.ingest import market_channel_usernames


def dashboard_redirect(request):
    return redirect("admin:index")


@require_http_methods(["GET", "POST"])
def market_extract_view(request):
    status = extract_status()
    names = status["channel_names"] or market_channel_usernames()
    selected = (request.POST.get("channel") or request.GET.get("channel") or (names[0] if names else "")).strip().lstrip("@")
    force_review = request.method != "POST" or request.POST.get("force_review") == "on"
    run = None
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "one_post":
            run = extract_one_post(selected, force_review=force_review)
        elif action == "extract":
            run = extract_channel(selected, force_review=force_review)
        else:
            run = {
                "ok": False,
                "logs": [
                    {
                        "level": "error",
                        "message": "Unknown action. Use Extract 1 post or Run extraction.",
                        "detail": "",
                        "time": "",
                    }
                ],
                "result": "error",
            }
        status = extract_status()
    context = {
        **admin.site.each_context(request),
        "title": "Manual extract",
        "subtitle": "Fetch a channel post and convert it with the LLM",
        "status": status,
        "selected": selected,
        "force_review": force_review,
        "run": run,
    }
    return render(request, "admin/market_extract.html", context)


def install_admin_dashboard() -> None:
    if getattr(admin.site, "_koolbar_dashboard_urls", False):
        return

    original = admin.site.get_urls

    def get_urls():
        return [
            path("dashboard/", admin.site.admin_view(dashboard_redirect), name="dashboard"),
            path("market/extract/", admin.site.admin_view(market_extract_view), name="market_extract"),
        ] + original()

    admin.site.get_urls = get_urls
    admin.site._koolbar_dashboard_urls = True
