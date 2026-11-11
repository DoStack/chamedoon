"""The Mini App is the SPA at the main URLs: courier at /app/, housing at /app/home/.

Old addresses keep working: /app/v2/... and the template pages' deep paths answer 301 to the same screen of the app,
with their query (Telegram launch data) kept. The template views stay in the code but are no longer routed, except
the session sign-in they and outreach links still use.
"""

from django.urls import path, re_path

from miniapp import views as miniapp_views
from spa import views

# Template page → the app's own path for it. `{pk}` is filled from the URL.
MOVED = [
    (r"^app/v2/courier/(?P<rest>.*)$", "/app/{rest}"),
    (r"^app/v2/(?P<rest>.*)$", "/app/home/{rest}"),
    (r"^app/courier/?$", "/app/"),
    (r"^app/courier/alerts?/?$", "/app/alerts"),
    (r"^app/demand/new/?$", "/app/post/package"),
    (r"^app/supply/new/?$", "/app/post/trip"),
    (r"^app/requests/?$", "/app/me/requests"),
    (r"^app/requests/(?P<pk>\d+)(?:/created)?/?$", "/app/me/requests/{pk}"),
    (r"^app/matches/?$", "/app/me/matches"),
    (r"^app/matches/(?P<pk>\d+)/?$", "/app/me/matches/{pk}"),
    (r"^app/explore/?$", "/app/"),
    (r"^app/explore/(?P<pk>\d+)/?$", "/app/request/{pk}"),
    (r"^(?:.+/)?explore_(?P<pk>\d+)/?$", "/app/request/{pk}"),
    (r"^app/support/?$", "/app/me/support"),
    (r"^app/support/new/?$", "/app/me/support/new"),
    (r"^app/support/(?P<pk>\d+)/?$", "/app/me/support/{pk}"),
    (r"^app/about/?$", "/app/guide"),
    (r"^app/home/list/?$", "/app/home/"),
    (r"^app/home/new/?$", "/app/home/post"),
    (r"^app/home/mine/?$", "/app/home/mine"),
    (r"^app/home/alerts/(?P<pk>\d+)/open/?$", "/app/home/alerts/{pk}"),
    (r"^app/home/(?:home_)?(?P<pk>\d+)/?$", "/app/home/listing/{pk}"),
]

urlpatterns = [
    *[re_path(pattern, views.moved(target), name=f"spa-moved-{index}") for index, (pattern, target) in enumerate(MOVED)],
    # Session sign-in and outreach clicks still go through the template views.
    path("app/login/", miniapp_views.login_view, name="miniapp-login"),
    path("app/logout/", miniapp_views.logout_view, name="miniapp-logout"),
    path("app/o/<str:token>/", miniapp_views.outreach_open, name="miniapp-outreach-open"),
    re_path(r"^app(?:/.*)?$", views.shell, name="spa-shell"),
]
