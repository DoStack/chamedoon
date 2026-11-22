from django.contrib import admin

from django import forms

from adminyar.models import AdminInvite, AdminTouch, AdminUser, AuditLog, ReadyPost
from adminyar.posts import blocked


class ReadyPostForm(forms.ModelForm):
    class Meta:
        model = ReadyPost
        fields = ("category", "channel", "message_id", "title", "enabled")

    def clean_title(self):
        title = self.cleaned_data["title"]
        if blocked(title):
            raise forms.ValidationError("The scam-safety tips post was rejected (decisions.md 2026-11-11) and is never offered.")
        return title


@admin.register(ReadyPost)
class ReadyPostAdmin(admin.ModelAdmin):
    form = ReadyPostForm
    list_display = ("title", "category", "channel", "message_id", "enabled", "views", "views_at")
    list_filter = ("category", "enabled", "channel")
    readonly_fields = ("views", "views_at", "created_at")


@admin.register(AdminUser)
class AdminUserAdmin(admin.ModelAdmin):
    list_display = ("telegram_user_id", "name", "role", "team_account", "code", "active", "last_seen")
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


@admin.register(AdminTouch)
class AdminTouchAdmin(admin.ModelAdmin):
    list_display = ("created_at", "admin_user", "via", "telegram_user_id", "channel", "first", "new_user")
    list_filter = ("admin_user", "via", "first", "new_user")
    search_fields = ("telegram_user_id",)
    readonly_fields = ("telegram_user_id", "admin_user", "via", "start_param", "channel", "first", "new_user", "created_at")

    def has_add_permission(self, request):
        return False


@admin.register(AdminInvite)
class AdminInviteAdmin(admin.ModelAdmin):
    list_display = ("admin_user", "channel", "link", "name", "created_at")
    list_filter = ("admin_user",)
    readonly_fields = ("admin_user", "channel", "link", "name", "created_at")

    def has_add_permission(self, request):
        return False
