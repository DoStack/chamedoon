"""Routes of the web access layer. Included from config/urls.py above /api/v1/, so the housing contract stays as it is."""

from django.urls import path

from web import stats_views

urlpatterns = [
    path("api/v1/public/stats/", stats_views.public_stats, name="web-public-stats"),
    path("api/v1/public/stats/<slug:city>.csv", stats_views.public_stats_csv, name="web-public-stats-csv"),
]
