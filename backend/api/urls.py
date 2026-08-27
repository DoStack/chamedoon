from django.urls import include, path
from rest_framework.routers import DefaultRouter

from api.bot_views import bot_create_request, bot_sync_user, bot_user_summary
from api.explore_views import ExploreViewSet
from api.match_views import MatchViewSet
from api.request_views import RequestViewSet, categories, locations
from api.views import health, me, telegram_auth

router = DefaultRouter()
router.register("requests", RequestViewSet, basename="item-request")
router.register("matches", MatchViewSet, basename="match")
router.register("explore", ExploreViewSet, basename="explore")

urlpatterns = [
    path("health/", health, name="health"),
    path("auth/telegram/", telegram_auth, name="telegram-auth"),
    path("me/", me, name="me"),
    path("categories/", categories, name="categories"),
    path("locations/", locations, name="locations"),
    path("bot/sync-user/", bot_sync_user, name="bot-sync-user"),
    path("bot/requests/", bot_create_request, name="bot-create-request"),
    path("bot/users/<int:telegram_user_id>/summary/", bot_user_summary, name="bot-user-summary"),
    path("", include(router.urls)),
]
