# SPIKE P0: throwaway, delete before P1.
from django.urls import path, re_path

from spike_p0 import views

urlpatterns = [
    path("spike-p0/cached.json", views.cached),
    path("spike-p0/diag.json", views.diag),
    re_path(r"^app/v2-spike/(?P<path>.*)$", views.shell),
]
