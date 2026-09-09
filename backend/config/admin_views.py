from django.contrib import admin
from django.shortcuts import redirect
from django.urls import path


def dashboard_redirect(request):
    return redirect("admin:index")


def install_admin_dashboard() -> None:
    if getattr(admin.site, "_koolbar_dashboard_urls", False):
        return

    original = admin.site.get_urls

    def get_urls():
        return [
            path("dashboard/", admin.site.admin_view(dashboard_redirect), name="dashboard"),
        ] + original()

    admin.site.get_urls = get_urls
    admin.site._koolbar_dashboard_urls = True
