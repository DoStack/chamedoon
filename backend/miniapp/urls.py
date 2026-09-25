from django.urls import path
from django.views.generic import RedirectView

from miniapp import support_views, views

urlpatterns = [
    path("dashboard/", RedirectView.as_view(url="/admin/", query_string=False), name="dashboard"),
    path("", views.landing, name="landing"),
    path("how-it-works/", views.how_it_works, name="how-it-works"),
    path("browse/", views.public_browse, name="public-browse"),
    path("browse/<int:pk>/", views.public_browse_detail, name="public-browse-detail"),
    path("app/login/", views.login_view, name="miniapp-login"),
    path("app/logout/", views.logout_view, name="miniapp-logout"),
    path("app/locale/", views.set_locale, name="miniapp-locale"),
    path("app/", views.home, name="miniapp-home"),
    path("app/about/", views.how_it_works, name="miniapp-about"),
    path("app/demand/new/", views.demand_new, name="miniapp-demand-new"),
    path("app/supply/new/", views.supply_new, name="miniapp-supply-new"),
    path("app/requests/", views.requests_list, name="miniapp-requests"),
    path("app/requests/<int:pk>/created/", views.request_created, name="miniapp-request-created"),
    path("app/requests/<int:pk>/", views.request_detail, name="miniapp-request-detail"),
    path("app/matches/", views.matches_list, name="miniapp-matches"),
    path("app/matches/<int:pk>/", views.match_detail, name="miniapp-match-detail"),
    path("app/explore/", views.explore, name="miniapp-explore"),
    path("app/explore/<int:pk>/", views.explore_detail, name="miniapp-explore-detail"),
    path("app/o/<str:token>/", views.outreach_open, name="miniapp-outreach-open"),
    path("app/support/", support_views.support_list, name="miniapp-support"),
    path("app/support/new/", support_views.support_create, name="miniapp-support-new"),
    path("app/support/<int:pk>/", support_views.support_detail, name="miniapp-support-detail"),
]
