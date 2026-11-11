from django.urls import path
from django.views.generic import RedirectView

from miniapp import views

urlpatterns = [
    path("dashboard/", RedirectView.as_view(url="/admin/", query_string=False), name="dashboard"),
    path("", views.landing, name="landing"),
    path("how-it-works/", views.how_it_works, name="how-it-works"),
    path("browse/", views.public_browse, name="public-browse"),
    path("browse/<int:pk>/", views.public_browse_detail, name="public-browse-detail"),
    # The /app/ pages are the SPA now (spa.urls). Their views stay in the code, unrouted; sign-in and outreach clicks
    # are routed from spa.urls.
]
