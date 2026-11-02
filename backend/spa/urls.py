from django.urls import re_path

from spa import views

urlpatterns = [
    re_path(r"^app/v2/.*$", views.shell, name="spa-shell"),
]
