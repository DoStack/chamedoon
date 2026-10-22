from __future__ import annotations

from django import forms
from django.contrib import admin, messages
from django.db.models import Q
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from unfold.admin import ModelAdmin

from outreach.models import (
    OutreachAccount,
    OutreachMessage,
    OutreachOptOut,
    OutreachSettings,
    OutreachState,
    OutreachStatus,
)
from outreach.queue import approve_messages, hold_messages, release_messages, skip_messages


@admin.register(OutreachMessage)
class OutreachMessageAdmin(ModelAdmin):
    list_display = (
        "id",
        "recipient_username",
        "status",
        "account",
        "text_start",
        "error_code",
        "created_at",
        "claimed_at",
        "sent_at",
        "opened_at",
        "replied_at",
    )
    list_filter = ("status", ("created_at", admin.DateFieldListFilter), ("sent_at", admin.DateFieldListFilter), "account", "error_code")
    date_hierarchy = "created_at"
    search_fields = ("recipient_username", "token", "recipient_telegram_id", "text")
    ordering = ("-created_at",)
    list_select_related = ("account",)
    actions = ("approve_selected", "skip_selected", "hold_selected", "release_selected")
    fields = (
        "recipient",
        "recipient_username",
        "recipient_telegram_id",
        "demand_request",
        "source_post",
        "status",
        "account",
        "error_code",
        "error_detail",
        "attempts",
        "preview_text",
        "matched_travelers",
        "text",
        "created_at",
        "claimed_at",
        "sent_at",
        "opened_at",
        "opened_by",
        "replied_at",
    )
    readonly_fields = fields

    def has_add_permission(self, request) -> bool:
        return False

    @admin.display(description="Source post")
    def source_post(self, obj: OutreachMessage) -> str:
        return obj.demand_request.source_url or "—"

    @admin.display(description="Text")
    def text_start(self, obj: OutreachMessage) -> str:
        first = (obj.text or "").strip().splitlines()[0] if obj.text else ""
        return first[:70]

    @admin.display(description="Text it would be sent with now")
    def preview_text(self, obj: OutreachMessage):
        from outreach.services import current_text

        text = current_text(obj, timezone.now()) if obj.status in {OutreachStatus.QUEUED, OutreachStatus.HELD} else obj.text
        return format_html('<pre dir="auto" style="white-space:pre-wrap;margin:0">{}</pre>', text)

    @admin.display(description="Matched travelers (live)")
    def matched_travelers(self, obj: OutreachMessage):
        from outreach.services import valid_matches

        matches = valid_matches(obj.demand_request, timezone.now())
        if not matches:
            return "No live match above the threshold: a claim would skip this message."
        rows = format_html_join(
            "",
            "<tr><td>@{}</td><td>{}</td><td>{} to {}</td><td>{}</td><td>{}</td></tr>",
            (
                (
                    match.supply_request.user.telegram_username or "-",
                    match.supply_request.flight_date or "",
                    match.supply_request.date_from,
                    match.supply_request.date_to,
                    match.supply_request.capacity_kg if match.supply_request.capacity_kg is not None else "",
                    match.score,
                )
                for match in matches
            ),
        )
        return format_html(
            "<table><thead><tr><th>Traveler</th><th>Flight</th><th>Window</th><th>kg</th><th>Score</th></tr></thead><tbody>{}</tbody></table>",
            rows,
        )

    @admin.action(description="Approve: queue selected held or skipped messages")
    def approve_selected(self, request, queryset) -> None:
        self.message_user(request, f"Queued {approve_messages(queryset)} message(s).", messages.SUCCESS)

    @admin.action(description="Do not send selected (skip)")
    def skip_selected(self, request, queryset) -> None:
        self.message_user(request, f"Skipped {skip_messages(queryset)} message(s).", messages.SUCCESS)

    @admin.action(description="Hold selected for review")
    def hold_selected(self, request, queryset) -> None:
        self.message_user(request, f"Held {hold_messages(queryset)} queued message(s).", messages.SUCCESS)

    @admin.action(description="Release held messages (queue them again)")
    def release_selected(self, request, queryset) -> None:
        self.message_user(request, f"Released {release_messages(queryset)} held message(s).", messages.SUCCESS)


@admin.register(OutreachOptOut)
class OutreachOptOutAdmin(ModelAdmin):
    list_display = ("telegram_username", "telegram_user_id", "source", "created_at")
    list_filter = ("source",)
    search_fields = ("telegram_username", "telegram_user_id")
    fields = ("telegram_username", "telegram_user_id", "source")

    def get_changeform_initial_data(self, request) -> dict:
        return {"source": "admin"}

    def save_model(self, request, obj, form, change) -> None:
        obj.telegram_username = (obj.telegram_username or "").strip().lstrip("@")
        super().save_model(request, obj, form, change)
        # Same as an opt-out by reply: nothing still waiting goes to this person.
        query = Q()
        if obj.telegram_username:
            query |= Q(recipient_username__iexact=obj.telegram_username)
        if obj.telegram_user_id:
            query |= Q(recipient_telegram_id=obj.telegram_user_id)
        if query:
            OutreachMessage.objects.filter(query, status__in=(OutreachStatus.QUEUED, OutreachStatus.HELD)).update(
                status=OutreachStatus.SKIPPED, error_code="opted_out", updated_at=timezone.now()
            )


@admin.register(OutreachState)
class OutreachStateAdmin(ModelAdmin):
    list_display = ("__str__", "stopped", "paused_until", "pause_reason", "last_heartbeat_at", "last_built_at")
    fields = ("stopped", "paused_until", "pause_reason", "last_heartbeat_at", "last_built_at")
    readonly_fields = ("last_heartbeat_at", "last_built_at")

    def has_add_permission(self, request) -> bool:
        return not OutreachState.objects.exists()

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


SWITCH_CHOICES = (("", "Server default (environment)"), ("true", "On"), ("false", "Off"))


class OutreachSettingsForm(forms.ModelForm):
    enabled = forms.TypedChoiceField(
        label="Outreach (build the queue)",
        choices=SWITCH_CHOICES,
        required=False,
        coerce=lambda value: {"true": True, "false": False}.get(value),
        empty_value=None,
    )
    sending_enabled = forms.TypedChoiceField(
        label="Sending",
        choices=SWITCH_CHOICES,
        required=False,
        coerce=lambda value: {"true": True, "false": False}.get(value),
        empty_value=None,
    )

    class Meta:
        model = OutreachSettings
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("enabled", "sending_enabled"):
            value = getattr(self.instance, name, None)
            self.initial[name] = "" if value is None else ("true" if value else "false")
            self.fields[name].help_text = OutreachSettings._meta.get_field(name).help_text

    def clean(self):
        data = super().clean()
        for name, high in (("send_start_hour", 23), ("send_end_hour", 24), ("min_score", 100)):
            value = data.get(name)
            if value is not None and value > high:
                self.add_error(name, f"Use 0 to {high}.")
        start, end = data.get("send_start_hour"), data.get("send_end_hour")
        if start is not None and end is not None and start >= end:
            self.add_error("send_end_hour", "The window must end after it starts.")
        return data


@admin.register(OutreachSettings)
class OutreachSettingsAdmin(ModelAdmin):
    form = OutreachSettingsForm
    fieldsets = (
        (
            "Switches",
            {
                "fields": ("enabled", "sending_enabled"),
                "description": "Take effect on the next claim or build, with no redeploy. To stop sending right away, also see Outreach state.",
            },
        ),
        ("Limits", {"fields": ("daily_limit", "account_daily_limit", "min_gap_minutes")}),
        ("Sending window (Tehran time)", {"fields": ("send_start_hour", "send_end_hour")}),
        ("Who gets a message", {"fields": ("min_score", "max_post_age_days", "cooldown_days")}),
        ("In effect now", {"fields": ("effective_now",)}),
    )
    readonly_fields = ("effective_now",)

    @admin.display(description="Values in use")
    def effective_now(self, obj):
        from outreach import conf

        return format_html(
            "<table>{}</table>",
            format_html_join(
                "",
                "<tr><td>{}</td><td><strong>{}</strong></td><td>{}</td></tr>",
                ((name.replace("_", " "), value, f"from {origin}") for name, (value, origin) in conf.effective().items()),
            ),
        )

    def has_add_permission(self, request) -> bool:
        return not OutreachSettings.objects.exists()

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def changelist_view(self, request, extra_context=None):
        return redirect(reverse("admin:outreach_outreachsettings_change", args=[OutreachSettings.load().pk]))


@admin.register(OutreachAccount)
class OutreachAccountAdmin(ModelAdmin):
    list_display = ("name", "enabled", "daily_limit", "min_gap_minutes", "sent_today", "last_sent_at", "note")
    list_editable = ("enabled", "daily_limit", "min_gap_minutes")
    fields = ("name", "enabled", "daily_limit", "min_gap_minutes", "note", "last_sent_at")
    readonly_fields = ("last_sent_at",)
    actions = ("enable_selected", "disable_selected")

    @admin.display(description="Attempted today (Tehran)")
    def sent_today(self, obj: OutreachAccount) -> int:
        from outreach.accounts import attempted_today_for

        return attempted_today_for(obj, timezone.now())

    @admin.action(description="Switch on selected")
    def enable_selected(self, request, queryset) -> None:
        self.message_user(request, f"Switched on {queryset.update(enabled=True)} account(s).", messages.SUCCESS)

    @admin.action(description="Switch off selected")
    def disable_selected(self, request, queryset) -> None:
        self.message_user(request, f"Switched off {queryset.update(enabled=False)} account(s).", messages.SUCCESS)
