from django.contrib import admin
from unfold.admin import ModelAdmin

from home.models import HomeListing, HomePost, HomeSource


@admin.register(HomeSource)
class HomeSourceAdmin(ModelAdmin):
    list_display = ("username", "country", "default_city", "default_currency", "kind", "enabled", "is_agency", "last_run_at")
    list_filter = ("country", "enabled", "is_agency", "kind")
    search_fields = ("username", "title")
    readonly_fields = ("newest_message_id", "oldest_message_id", "backfill_complete", "last_run_at", "last_error")


@admin.register(HomePost)
class HomePostAdmin(ModelAdmin):
    list_display = ("posted_at", "source", "status", "reject_reason", "model", "telegram_message_id")
    list_filter = ("status", "reject_reason", "source")
    search_fields = ("text", "author_username")
    date_hierarchy = "posted_at"
    readonly_fields = tuple(f.name for f in HomePost._meta.fields)


@admin.register(HomeListing)
class HomeListingAdmin(ModelAdmin):
    list_display = (
        "last_seen",
        "kind",
        "property_type",
        "city",
        "area",
        "price_amount",
        "price_currency",
        "price_basis",
        "lister_type",
        "status",
        "needs_review",
    )
    list_filter = ("country", "kind", "property_type", "status", "needs_review", "lister_type")
    search_fields = ("city", "area", "summary_fa", "contact_telegram", "contact_phone")
    ordering = ("-last_seen",)
    readonly_fields = ("post", "canonical", "dedup_key", "first_seen", "last_seen", "created_at", "updated_at")
