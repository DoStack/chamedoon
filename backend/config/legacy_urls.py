"""config.urls with the template pages routed again, before the SPA. Tests of the template views only."""

from django.urls import include, path

from config.urls import urlpatterns as _main

urlpatterns = [path("", include("miniapp.legacy_urls")), *_main]
