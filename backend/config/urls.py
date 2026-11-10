from django.contrib import admin
from django.urls import include, path, re_path

from config import unfold_auth  # noqa: F401  registers Unfold staff User/Group admin
from config.admin_views import install_admin_dashboard
from config.pulse_proxy import pulse_proxy

install_admin_dashboard()

urlpatterns = [
    path("api/v1/courier/", include("courier_api.urls")),
    # Pulse runs on the Chamedoon VM; this only forwards to it (docs/pulse.md). It must sit above the admin.
    re_path(r"^admin/pulse/(?P<rest>.*)$", pulse_proxy),
    path("admin/", admin.site.urls),
    path("api/v1/", include("home_api.urls")),
    path("api/", include("api.urls")),
    path("", include("spa.urls")),
    path("", include("miniapp.urls")),
]
