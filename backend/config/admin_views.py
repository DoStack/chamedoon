from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import path

from config.dashboard import build_dashboard_context


def dashboard_view(request):
    context = {
        **admin.site.each_context(request),
        "title": "Dashboard",
        "subtitle": "Operations reports",
    }
    context = build_dashboard_context(request, context)
    return TemplateResponse(request, "admin/dashboard.html", context)


def install_admin_dashboard() -> None:
    if getattr(admin.site, "_koolbar_dashboard_urls", False):
        return

    original = admin.site.get_urls

    def get_urls():
        return [
            path("dashboard/", admin.site.admin_view(dashboard_view), name="dashboard"),
        ] + original()

    admin.site.get_urls = get_urls
    admin.site._koolbar_dashboard_urls = True
