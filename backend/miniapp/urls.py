from django.urls import path, re_path
from django.views.generic import RedirectView

from miniapp import home_views, support_views, views

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
    path("app/explore_<int:pk>/", views.explore_start, name="miniapp-explore-start"),
    path("app/explore/explore_<int:pk>/", views.explore_start, name="miniapp-explore-start-nested"),
    path("app/explore/<int:pk>/", views.explore_detail, name="miniapp-explore-detail"),
    path("app/o/<str:token>/", views.outreach_open, name="miniapp-outreach-open"),
    path("app/home/", home_views.home_landing, name="miniapp-home-landing"),
    path("app/home/list/", home_views.homes, name="miniapp-homes"),
    path("app/home/list/count/", home_views.homes_count, name="miniapp-homes-count"),
    path("app/home/alerts/", home_views.alerts_page, name="miniapp-home-alerts"),
    path("app/home/alerts/save/", home_views.alert_save, name="miniapp-home-alert-save"),
    path("app/home/alerts/<int:pk>/open/", home_views.alert_open, name="miniapp-home-alert-open"),
    path("app/home/new/", home_views.home_new, name="miniapp-home-new"),
    path("app/home/mine/", home_views.home_mine, name="miniapp-home-mine"),
    path("app/home/home_<int:pk>/", home_views.home_start, name="miniapp-home-start"),
    path("app/home/<int:pk>/", home_views.home_detail, name="miniapp-home-detail"),
    path("app/support/", support_views.support_list, name="miniapp-support"),
    path("app/support/new/", support_views.support_create, name="miniapp-support-new"),
    path("app/support/<int:pk>/", support_views.support_detail, name="miniapp-support-detail"),
    # Channel buttons open startapp=explore_<id> as a path under the Mini App URL.
    re_path(r"^(?:.+/)?explore_(?P<pk>[0-9]+)/$", views.explore_start, name="miniapp-explore-start-any"),
]
