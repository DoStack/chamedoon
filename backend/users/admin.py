from __future__ import annotations

from django.contrib import admin, messages
from django.db.models import QuerySet
from unfold.admin import ModelAdmin

from users.models import BotStart, User
from users.services import activate_user, deactivate_user

admin.site.site_header = "Chamedoon Back Office"
admin.site.site_title = "Chamedoon Admin"
admin.site.index_title = "Dashboard"


class StartedBotFilter(admin.SimpleListFilter):
    title = "started bot"
    parameter_name = "started_bot"

    def lookups(self, request, model_admin):
        return (("yes", "Yes"), ("no", "No"))

    def queryset(self, request, queryset: QuerySet[User]):
        if self.value() == "yes":
            return queryset.filter(first_started_at__isnull=False)
        if self.value() == "no":
            return queryset.filter(first_started_at__isnull=True)
        return queryset


class OriginFilter(admin.SimpleListFilter):
    title = "origin"
    parameter_name = "origin"

    def lookups(self, request, model_admin):
        return (
            ("organic", "Organic"),
            ("market", "Market extracted"),
            ("converted", "Market + bot"),
            ("unknown", "Unknown"),
        )

    def queryset(self, request, queryset: QuerySet[User]):
        value = self.value()
        if value == "organic":
            return queryset.filter(first_started_at__isnull=False, from_market=False)
        if value == "market":
            return queryset.filter(from_market=True, first_started_at__isnull=True)
        if value == "converted":
            return queryset.filter(from_market=True, first_started_at__isnull=False)
        if value == "unknown":
            return queryset.filter(from_market=False, first_started_at__isnull=True)
        return queryset


@admin.register(User)
class UserAdmin(ModelAdmin):
    list_display = (
        "id",
        "telegram_user_id",
        "telegram_username",
        "first_name",
        "origin",
        "from_market",
        "start_count",
        "first_started_at",
        "is_active",
        "created_at",
    )
    list_filter = ("is_active", "from_market", StartedBotFilter, OriginFilter)
    search_fields = ("telegram_user_id", "telegram_username", "first_name", "last_name")
    readonly_fields = (
        "telegram_user_id",
        "first_started_at",
        "last_started_at",
        "start_count",
        "created_at",
        "updated_at",
    )
    actions = ("deactivate_users", "activate_users")

    @admin.display(description="Origin")
    def origin(self, obj: User) -> str:
        return obj.origin

    @admin.action(description="Deactivate selected users and cancel their active requests")
    def deactivate_users(self, request, queryset):
        count = 0
        for user in queryset:
            deactivate_user(user)
            count += 1
        self.message_user(request, f"Deactivated {count} user(s).", messages.SUCCESS)

    @admin.action(description="Re-activate selected users")
    def activate_users(self, request, queryset):
        count = 0
        for user in queryset:
            activate_user(user)
            count += 1
        self.message_user(request, f"Activated {count} user(s).", messages.SUCCESS)


@admin.register(BotStart)
class BotStartAdmin(ModelAdmin):
    list_display = ("id", "user", "telegram_user_id", "payload", "started_at")
    list_filter = ("started_at",)
    search_fields = ("telegram_user_id", "payload", "user__telegram_username", "user__first_name")
    readonly_fields = ("user", "telegram_user_id", "payload", "started_at")
    ordering = ("-started_at",)
