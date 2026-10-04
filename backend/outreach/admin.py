from __future__ import annotations

from django.contrib import admin, messages
from django.utils import timezone
from unfold.admin import ModelAdmin

from outreach.models import OutreachMessage, OutreachOptOut, OutreachState, OutreachStatus


@admin.register(OutreachMessage)
class OutreachMessageAdmin(ModelAdmin):
    list_display = (
        "id",
        "recipient_username",
        "status",
        "error_code",
        "created_at",
        "sent_at",
        "opened_at",
        "replied_at",
    )
    list_filter = ("status", "error_code")
    search_fields = ("recipient_username", "token", "recipient_telegram_id")
    ordering = ("-created_at",)
    actions = ("skip_selected", "hold_selected", "release_selected")
    fields = (
        "recipient",
        "recipient_username",
        "recipient_telegram_id",
        "demand_request",
        "source_post",
        "status",
        "error_code",
        "error_detail",
        "attempts",
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

    @admin.action(description="Do not send selected (skip)")
    def skip_selected(self, request, queryset) -> None:
        skipped = queryset.filter(status=OutreachStatus.QUEUED).update(
            status=OutreachStatus.SKIPPED,
            error_code="admin",
            updated_at=timezone.now(),
        )
        self.message_user(request, f"Skipped {skipped} queued message(s).", messages.SUCCESS)

    @admin.action(description="Hold selected for review")
    def hold_selected(self, request, queryset) -> None:
        held = queryset.filter(status=OutreachStatus.QUEUED).update(
            status=OutreachStatus.HELD,
            error_code="admin_hold",
            updated_at=timezone.now(),
        )
        self.message_user(request, f"Held {held} queued message(s).", messages.SUCCESS)

    @admin.action(description="Release held messages (queue them again)")
    def release_selected(self, request, queryset) -> None:
        released = queryset.filter(status=OutreachStatus.HELD).update(
            status=OutreachStatus.QUEUED,
            error_code="",
            updated_at=timezone.now(),
        )
        self.message_user(request, f"Released {released} held message(s).", messages.SUCCESS)


@admin.register(OutreachOptOut)
class OutreachOptOutAdmin(ModelAdmin):
    list_display = ("telegram_username", "telegram_user_id", "source", "created_at")
    search_fields = ("telegram_username", "telegram_user_id")
    fields = ("telegram_username", "telegram_user_id", "source")

    def get_changeform_initial_data(self, request) -> dict:
        return {"source": "admin"}


@admin.register(OutreachState)
class OutreachStateAdmin(ModelAdmin):
    list_display = ("__str__", "stopped", "paused_until", "pause_reason", "last_heartbeat_at", "last_built_at")
    fields = ("stopped", "paused_until", "pause_reason", "last_heartbeat_at", "last_built_at")
    readonly_fields = ("last_heartbeat_at", "last_built_at")

    def has_add_permission(self, request) -> bool:
        return not OutreachState.objects.exists()

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
