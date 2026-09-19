from __future__ import annotations

from django.contrib import admin, messages
from unfold.admin import ModelAdmin

from users.models import User
from users.services import activate_user, deactivate_user

admin.site.site_header = "Chamedoon Back Office"
admin.site.site_title = "Chamedoon Admin"
admin.site.index_title = "Dashboard"


@admin.register(User)
class UserAdmin(ModelAdmin):
    list_display = (
        "id",
        "telegram_user_id",
        "telegram_username",
        "first_name",
        "last_name",
        "is_active",
        "created_at",
    )
    list_filter = ("is_active",)
    search_fields = ("telegram_user_id", "telegram_username", "first_name", "last_name")
    readonly_fields = ("telegram_user_id", "created_at", "updated_at")
    actions = ("deactivate_users", "activate_users")

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
