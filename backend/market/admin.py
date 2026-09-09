from django.contrib import admin
from unfold.admin import ModelAdmin

from market.models import MarketIngestState, MarketPost


@admin.register(MarketPost)
class MarketPostAdmin(ModelAdmin):
    list_display = (
        "posted_at",
        "role",
        "skip_reason",
        "route_label",
        "weight_kg",
        "views",
        "item_request",
        "telegram_message_id",
        "channel_username",
    )
    list_filter = ("role", "skip_reason", "origin_country", "destination_country", "channel_username")
    search_fields = ("text", "origin_city", "destination_city", "telegram_message_id")
    date_hierarchy = "posted_at"
    ordering = ("-posted_at",)
    readonly_fields = (
        "channel_username",
        "telegram_message_id",
        "posted_at",
        "text",
        "views",
        "has_photo",
        "role",
        "origin_city",
        "origin_country",
        "destination_city",
        "destination_country",
        "weight_kg",
        "source_url",
        "author_name",
        "author_username",
        "review_json",
        "reviewed_at",
        "item_request",
        "skip_reason",
        "migrated_at",
        "ingested_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def route_label(self, obj: MarketPost) -> str:
        return obj.route_label()

    route_label.short_description = "route"


@admin.register(MarketIngestState)
class MarketIngestStateAdmin(ModelAdmin):
    list_display = (
        "channel_username",
        "last_run_at",
        "last_created",
        "last_updated",
        "backfill_complete",
        "newest_message_id",
        "oldest_message_id",
    )
    readonly_fields = (
        "channel_username",
        "newest_message_id",
        "oldest_message_id",
        "backfill_complete",
        "last_run_at",
        "last_created",
        "last_updated",
        "last_error",
    )

    def has_add_permission(self, request):
        return False
