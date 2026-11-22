"""Routes of the web access layer. Included from config/urls.py above /api/v1/, so the housing contract stays as it is."""

from django.urls import path

from courier_api import public as courier_public
from web import auth_views, stats_views

urlpatterns = [
    path("api/v1/public/stats/", stats_views.public_stats, name="web-public-stats"),
    path("api/v1/public/courier/requests/", courier_public.PublicRequestListView.as_view(), name="courier-public-requests"),
    path("api/v1/public/courier/requests/facets/", courier_public.PublicFacetsView.as_view(), name="courier-public-facets"),
    path("api/v1/public/courier/requests/<int:pk>/", courier_public.PublicRequestDetailView.as_view(), name="courier-public-request"),
    path("api/v1/public/courier/locations/", courier_public.PublicLocationsView.as_view(), name="courier-public-locations"),
    path("api/v1/public/courier/guide/", courier_public.PublicGuideView.as_view(), name="courier-public-guide"),
    path("api/v1/auth/web/session/", auth_views.session_view, name="web-session"),
    path("api/v1/auth/web/start/", auth_views.start_view, name="web-login-start"),
    path("api/v1/auth/web/poll/", auth_views.poll_view, name="web-login-poll"),
    path("api/v1/auth/web/logout/", auth_views.logout_view, name="web-logout"),
    path("api/v1/auth/email/start/", auth_views.email_start_view, name="web-email-start"),
    path("api/v1/auth/email/verify/", auth_views.email_verify_view, name="web-email-verify"),
]
