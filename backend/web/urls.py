"""Routes of the web access layer. Included from config/urls.py above /api/v1/, so the housing contract stays as it is."""

from django.urls import path

from web import auth_views, stats_views

urlpatterns = [
    path("api/v1/public/stats/", stats_views.public_stats, name="web-public-stats"),
    path("api/v1/public/stats/<slug:city>.csv", stats_views.public_stats_csv, name="web-public-stats-csv"),
    path("api/v1/auth/web/session/", auth_views.session_view, name="web-session"),
    path("api/v1/auth/web/start/", auth_views.start_view, name="web-login-start"),
    path("api/v1/auth/web/poll/", auth_views.poll_view, name="web-login-poll"),
    path("api/v1/auth/web/logout/", auth_views.logout_view, name="web-logout"),
    path("api/v1/auth/email/start/", auth_views.email_start_view, name="web-email-start"),
    path("api/v1/auth/email/verify/", auth_views.email_verify_view, name="web-email-verify"),
]
