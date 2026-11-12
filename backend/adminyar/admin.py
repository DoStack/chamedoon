from django.contrib import admin

from adminyar.models import AdminUser, AuditLog


@admin.register(AdminUser)
class AdminUserAdmin(admin.ModelAdmin):
    list_display = ("telegram_user_id", "name", "role", "team_account", "active", "last_seen")
    list_filter = ("role", "active", "team_account")
    search_fields = ("name", "telegram_user_id")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "telegram_user_id", "admin_user", "action", "result")
    list_filter = ("action",)
    search_fields = ("telegram_user_id", "result")
    readonly_fields = ("telegram_user_id", "admin_user", "action", "args", "result", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
