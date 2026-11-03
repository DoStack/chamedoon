from django.contrib import admin

from aisearch.models import HomeSearchQuery, SearchSettings


@admin.register(SearchSettings)
class SearchSettingsAdmin(admin.ModelAdmin):
    list_display = ("__str__", "enabled", "llm_enabled", "user_daily_llm", "global_daily_llm")

    def has_add_permission(self, request) -> bool:
        return not SearchSettings.objects.exists()

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(HomeSearchQuery)
class HomeSearchQueryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "text", "city", "status", "source", "tokens")
    list_filter = ("status", "source", "city")
    search_fields = ("text",)
    readonly_fields = ("user", "text", "normalized", "country", "city", "status", "source", "tokens", "answer", "created_at", "cleared_at")

    def has_add_permission(self, request) -> bool:
        return False
