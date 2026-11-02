"""The URLs openapi.yaml is built from: the app's routes under their real prefix."""

from django.urls import include, path

urlpatterns = [
    path("api/v1/courier/", include("courier_api.urls")),
]
