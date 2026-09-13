from __future__ import annotations

from django.contrib import admin
from django.shortcuts import redirect, render
from django.urls import path
from django.views.decorators.http import require_http_methods

from market.extract import (
    convert_reviewed_post,
    convert_stored_posts,
    extract_all_channels,
    extract_catalog,
    extract_named_post,
    extract_one_post,
    extract_status,
    parse_telegram_post_ref,
    reset_extract_cursor,
)
from market.ingest import DEFAULT_EXTRACT_DAYS, clamp_lookback_days, market_channel_usernames


def dashboard_redirect(request):
    return redirect("admin:index")


@require_http_methods(["GET", "POST"])
def market_extract_view(request):
    status = extract_status()
    names = status["channel_names"] or market_channel_usernames()
    selected = (request.POST.get("channel") or request.GET.get("channel") or (names[0] if names else "")).strip().lstrip("@")
    post_url = (request.POST.get("post_url") or request.GET.get("post_url") or "").strip()
    days = clamp_lookback_days(
        request.POST.get("days") or request.GET.get("days"),
        default=DEFAULT_EXTRACT_DAYS,
    )
    force_review = request.method != "POST" or request.POST.get("force_review") == "on"
    run = None
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "one_post":
            run = extract_one_post(selected, force_review=force_review)
        elif action == "named_post":
            parsed = parse_telegram_post_ref(post_url)
            if parsed:
                selected = parsed[0]
            run = extract_named_post(selected, url=post_url, force_review=force_review)
        elif action == "extract":
            run = extract_all_channels(days=days)
        elif action == "convert_stored":
            run = convert_stored_posts(days=days)
        elif action == "reset_cursor":
            run = reset_extract_cursor(selected)
        elif action == "convert":
            run = convert_reviewed_post(request.POST.get("post_id"), request.POST)
            selected = (request.POST.get("channel") or selected).strip().lstrip("@")
        else:
            run = {
                "ok": False,
                "logs": [
                    {
                        "level": "error",
                        "message": "Unknown action. Use Extract 1 post, Extract this post, Run extraction, Convert stored posts, or Convert to request.",
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
        "subtitle": "Paste a Telegram post URL, walk one message at a time, or crawl every configured channel",
        "status": status,
        "selected": selected,
        "post_url": post_url,
        "days": days,
        "selected_cursor": next(
            (row.get("extract_cursor_id") for row in status["channels"] if row["username"] == selected),
            None,
        ),
        "force_review": force_review,
        "run": run,
        "draft": (run or {}).get("draft"),
        "preview": (run or {}).get("preview"),
        "catalog": extract_catalog(),
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
