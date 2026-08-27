from django.contrib import admin
from django.urls import include, path

from config import unfold_auth  # noqa: F401  registers Unfold staff User/Group admin

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("api.urls")),
]
