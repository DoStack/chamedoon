from django.contrib import admin
from unfold.admin import ModelAdmin

from courier.models import CourierAlertSent, CourierIntro, CourierRouteAlert, CourierSettings


@admin.register(CourierSettings)
class CourierSettingsAdmin(ModelAdmin):
    list_display = ("__str__", "bot_chat_enabled", "intros_enabled", "alerts_enabled", "updated_at")
    fieldsets = (
        (
            "Messaging switches (all off until approved)",
            {"fields": ("bot_chat_enabled", "intros_enabled", "alerts_enabled", "intro_daily_limit", "alert_daily_cap")},
        ),
        ("Prohibited items («چه چیزهایی ممنوعه؟»)", {"fields": ("prohibited_text",)}),
    )

    def has_add_permission(self, request) -> bool:
        return not CourierSettings.objects.exists()

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(CourierIntro)
class CourierIntroAdmin(ModelAdmin):
    list_display = ("id", "requester", "target", "target_request", "status", "created_at", "answered_at")
    list_filter = ("status",)
    raw_id_fields = ("requester", "target", "target_request", "requester_request")


@admin.register(CourierRouteAlert)
class CourierRouteAlertAdmin(ModelAdmin):
    list_display = ("id", "telegram_user_id", "role", "origin_city", "destination_city", "date_to", "weight_kg", "active", "last_sent_at")
    list_filter = ("role", "active")
    raw_id_fields = ("user",)


@admin.register(CourierAlertSent)
class CourierAlertSentAdmin(ModelAdmin):
    list_display = ("id", "alert", "request", "sent_at")
    raw_id_fields = ("alert", "request")
