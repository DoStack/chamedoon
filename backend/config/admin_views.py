from django.contrib import admin
from django.template.response import TemplateResponse

from config.dashboard import build_dashboard_context


def dashboard_view(request):
    context = {
        **admin.site.each_context(request),
        "title": "Dashboard",
        "subtitle": "Operations reports",
    }
    context = build_dashboard_context(request, context)
    return TemplateResponse(request, "admin/dashboard.html", context)
