from django.contrib import admin
from django.urls import include, path

from config import unfold_auth  # noqa: F401  registers Unfold staff User/Group admin
from config.admin_views import install_admin_dashboard

install_admin_dashboard()

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("api.urls")),
    path("", include("miniapp.urls")),
]
