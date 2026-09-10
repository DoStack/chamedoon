from django.urls import include, path
from rest_framework.routers import DefaultRouter

from api.bot_views import bot_create_request, bot_sync_user, bot_user_summary
from api.cron_views import (
    auto_close_tickets_cron,
    expire_requests_cron,
    ingest_market_channel_cron,
    migrate_market_posts_cron,
    retry_market_llm_cron,
)
from api.explore_views import ExploreViewSet
from api.match_views import MatchViewSet
from api.request_views import RequestViewSet, categories, locations
from api.support_views import SupportTicketViewSet
from api.telegram_webhook import telegram_webhook
from api.views import health, me, telegram_auth

router = DefaultRouter()
router.register("requests", RequestViewSet, basename="item-request")
router.register("matches", MatchViewSet, basename="match")
router.register("explore", ExploreViewSet, basename="explore")
router.register("support-tickets", SupportTicketViewSet, basename="support-ticket")

urlpatterns = [
    path("health/", health, name="health"),
    path("cron/market-channel/", ingest_market_channel_cron, name="cron-market-channel"),
    path("cron/market-migrate/", migrate_market_posts_cron, name="cron-market-migrate"),
    path("cron/market-llm-retry/", retry_market_llm_cron, name="cron-market-llm-retry"),
    path("cron/expire-requests/", expire_requests_cron, name="cron-expire-requests"),
    path("cron/auto-close-tickets/", auto_close_tickets_cron, name="cron-auto-close-tickets"),
    path("auth/telegram/", telegram_auth, name="telegram-auth"),
    path("telegram/webhook/", telegram_webhook, name="telegram-webhook"),
    path("me/", me, name="me"),
    path("categories/", categories, name="categories"),
    path("locations/", locations, name="locations"),
    path("bot/sync-user/", bot_sync_user, name="bot-sync-user"),
    path("bot/requests/", bot_create_request, name="bot-create-request"),
    path("bot/users/<int:telegram_user_id>/summary/", bot_user_summary, name="bot-user-summary"),
    path("", include(router.urls)),
]
