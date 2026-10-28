import re

from django import forms
from django.contrib import admin
from unfold.admin import ModelAdmin

from courier.models import CourierAlertSent, CourierIntro, CourierRouteAlert, CourierSettings


class CourierSettingsForm(forms.ModelForm):
    webhook_secret = forms.CharField(
        label="Courier bot webhook secret",
        required=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text=(
            "Letters, digits, _ and -. Blank uses TELEGRAM_WEBHOOK_SECRET. "
            "Easier: Admin → Telegram webhooks → Generate new secret (also re-sets the webhook)."
        ),
    )

    class Meta:
        model = CourierSettings
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.webhook_secret and "webhook_secret" in self.fields:
            self.fields["webhook_secret"].help_text = (
                "A secret is saved and is never shown. Leave blank to keep it, or type a new one to replace it "
                "(then set the webhook again in Admin → Telegram webhooks)."
            )

    def clean_webhook_secret(self) -> str:
        value = (self.cleaned_data.get("webhook_secret") or self.instance.webhook_secret or "").strip()
        if value and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
            raise forms.ValidationError("Use letters, digits, _ and - only (Telegram's rule for the secret token).")
        return value


@admin.register(CourierSettings)
class CourierSettingsAdmin(ModelAdmin):
    form = CourierSettingsForm
    list_display = ("__str__", "bot_chat_enabled", "intros_enabled", "alerts_enabled", "updated_at")
    fieldsets = (
        (
            "Messaging switches (all off until approved)",
            {"fields": ("bot_chat_enabled", "intros_enabled", "alerts_enabled", "intro_daily_limit", "alert_daily_cap")},
        ),
        ("Prohibited items («چه چیزهایی ممنوعه؟»)", {"fields": ("prohibited_text",)}),
        ("Bot webhook", {"fields": ("webhook_secret",)}),
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
