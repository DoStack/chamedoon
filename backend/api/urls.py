from django.urls import include, path
from rest_framework.routers import DefaultRouter

from api.bot_views import bot_create_request, bot_sync_user, bot_user_summary
from api.cron_views import (
    auto_close_tickets_cron,
    expire_requests_cron,
    ingest_market_channel_cron,
    market_convert_cron,
    market_extract_cron,
    market_publish_cron,
    migrate_market_posts_cron,
    outreach_build_cron,
    outreach_send_cron,
    retry_market_llm_cron,
)
from api.explore_views import ExploreViewSet
from api.match_views import MatchViewSet
from api.outreach_views import (
    outreach_build,
    outreach_claim,
    outreach_heartbeat,
    outreach_hold,
    outreach_opt_out,
    outreach_preview,
    outreach_release,
    outreach_reply,
    outreach_result,
)
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
    path("cron/market-extract/", market_extract_cron, name="cron-market-extract"),
    path("cron/market-convert/", market_convert_cron, name="cron-market-convert"),
    path("cron/market-publish/", market_publish_cron, name="cron-market-publish"),
    path("cron/market-migrate/", migrate_market_posts_cron, name="cron-market-migrate"),
    path("cron/market-llm-retry/", retry_market_llm_cron, name="cron-market-llm-retry"),
    path("cron/expire-requests/", expire_requests_cron, name="cron-expire-requests"),
    path("cron/auto-close-tickets/", auto_close_tickets_cron, name="cron-auto-close-tickets"),
    path("cron/outreach-build/", outreach_build_cron, name="cron-outreach-build"),
    # Four daily send slots (Vercel Hobby crons run once a day each): at most 4 DMs a day.
    path("cron/outreach-send/", outreach_send_cron, name="cron-outreach-send"),
    path("cron/outreach-send-2/", outreach_send_cron, name="cron-outreach-send-2"),
    path("cron/outreach-send-3/", outreach_send_cron, name="cron-outreach-send-3"),
    path("cron/outreach-send-4/", outreach_send_cron, name="cron-outreach-send-4"),
    path("outreach/build/", outreach_build, name="outreach-build"),
    path("outreach/claim/", outreach_claim, name="outreach-claim"),
    path("outreach/preview/", outreach_preview, name="outreach-preview"),
    path("outreach/heartbeat/", outreach_heartbeat, name="outreach-heartbeat"),
    path("outreach/opt-out/", outreach_opt_out, name="outreach-opt-out"),
    path("outreach/reply/", outreach_reply, name="outreach-reply"),
    path("outreach/<int:pk>/result/", outreach_result, name="outreach-result"),
    path("outreach/<int:pk>/hold/", outreach_hold, name="outreach-hold"),
    path("outreach/<int:pk>/release/", outreach_release, name="outreach-release"),
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
