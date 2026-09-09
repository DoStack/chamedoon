from django.contrib import admin
from django.urls import include, path

from config import unfold_auth  # noqa: F401  registers Unfold staff User/Group admin
from config.admin_views import dashboard_view

urlpatterns = [
    path("dashboard/", admin.site.admin_view(dashboard_view), name="dashboard"),
    path("admin/dashboard/", admin.site.admin_view(dashboard_view), name="admin-dashboard"),
    path("admin/", admin.site.urls),
    path("api/", include("api.urls")),
    path("", include("miniapp.urls")),
]
