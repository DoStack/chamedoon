from __future__ import annotations

from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.urls import reverse
from unfold.admin import ModelAdmin

from support.models import SupportMessage, SupportTicket, TicketStatus
from support.services import (
    add_admin_message,
    admin_sender_label,
    related_activity,
    set_ticket_status,
)


@admin.register(SupportTicket)
class SupportTicketAdmin(ModelAdmin):
    change_form_template = "admin/support/supportticket/change_form.html"
    list_display = (
        "id",
        "user",
        "subject",
        "status",
        "created_at",
        "last_activity",
    )
    list_filter = ("status", "subject")
    search_fields = (
        "id",
        "user__first_name",
        "user__telegram_username",
        "user__telegram_user_id",
        "messages__message",
    )
    readonly_fields = (
        "id",
        "user",
        "subject",
        "status",
        "created_at",
        "updated_at",
        "closed_at",
        "closed_by",
        "last_admin_message_at",
    )
    fields = (
        "id",
        "user",
        "subject",
        "status",
        "created_at",
        "updated_at",
        "closed_at",
        "closed_by",
        "last_admin_message_at",
    )
    inlines = []
    ordering = ("-updated_at", "-id")
    list_per_page = 50

    @admin.display(description="Last Activity", ordering="updated_at")
    def last_activity(self, obj: SupportTicket):
        return obj.updated_at

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["title"] = "Support Tickets"
        return super().changelist_view(request, extra_context=extra_context)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = extra_context or {}
        ticket = self.get_object(request, object_id) if object_id else None
        if ticket and request.method == "POST":
            action = (request.POST.get("support_action") or "").strip()
            if action:
                return self._handle_support_action(request, ticket, action)
        if ticket:
            extra_context.update(self._ticket_context(ticket))
        return super().changeform_view(request, object_id, form_url, extra_context)

    def _handle_support_action(self, request, ticket: SupportTicket, action: str):
        try:
            if action == "reply":
                add_admin_message(ticket, request.user, {"message": request.POST.get("reply")})
                self.message_user(request, "Reply sent. Ticket is now In Queue.", messages.SUCCESS)
            elif action in {TicketStatus.OPEN, TicketStatus.IN_QUEUE, TicketStatus.CLOSED, "close"}:
                set_ticket_status(ticket, TicketStatus.CLOSED if action == "close" else action)
                self.message_user(request, "Ticket status updated.", messages.SUCCESS)
            else:
                self.message_user(request, "Unknown action.", messages.ERROR)
        except ValidationError as exc:
            self.message_user(request, _validation_text(exc), messages.ERROR)
        return redirect(reverse("admin:support_supportticket_change", args=[ticket.pk]))

    def _ticket_context(self, ticket: SupportTicket) -> dict:
        related = related_activity(ticket.user)
        conversation = []
        for row in ticket.messages.all():
            conversation.append(
                {
                    "sender": _sender_label(ticket, row),
                    "sender_type": row.sender_type,
                    "message": row.message,
                    "created_at": row.created_at,
                }
            )
        return {
            "ticket_info": ticket,
            "conversation": conversation,
            "user_info": ticket.user,
            "user_display_name": " ".join(
                part for part in [ticket.user.first_name, ticket.user.last_name] if part
            ).strip()
            or ticket.user.first_name
            or "User",
            "related": related,
            "can_reply": ticket.status != TicketStatus.CLOSED,
            "status_label": ticket.get_status_display(),
            "demand_links": [_request_link(item) for item in related["demands"]],
            "supply_links": [_request_link(item) for item in related["supplies"]],
            "pending_links": [_match_link(item) for item in related["pending"]],
            "accepted_links": [_match_link(item) for item in related["accepted"]],
            "rejected_links": [_match_link(item) for item in related["rejected"]],
            "cancelled_links": [_match_link(item) for item in related["cancelled"]],
            "user_admin_url": reverse("admin:users_user_change", args=[ticket.user_id]),
        }


def _sender_label(ticket: SupportTicket, message: SupportMessage) -> str:
    if message.sender_type == "USER":
        return ticket.user.first_name or "User"
    if message.sender_type == "ADMIN":
        return admin_sender_label(message.sender_id)
    return "System"


def _request_link(item) -> dict:
    return {
        "label": f"#{item.pk} {item}",
        "url": reverse("admin:item_requests_itemrequest_change", args=[item.pk]),
        "status": item.status,
        "status_label": item.get_status_display(),
        "tone": _status_tone(item.status),
    }


def _match_link(item) -> dict:
    return {
        "label": f"#{item.pk} {item}",
        "url": reverse("admin:matching_match_change", args=[item.pk]),
        "status": item.status,
        "status_label": item.get_status_display(),
        "tone": _status_tone(item.status),
    }


def _status_tone(status: str) -> str:
    return {
        "OPEN": "ok",
        "ACTIVE": "ok",
        "ACCEPTED": "ok",
        "COMPLETED": "ok",
        "IN_QUEUE": "warn",
        "PENDING_APPROVAL": "warn",
        "CLOSED": "danger",
        "REJECTED": "danger",
        "CANCELLED": "danger",
        "EXPIRED": "muted",
    }.get(status, "muted")


def _validation_text(exc: ValidationError) -> str:
    if hasattr(exc, "message_dict"):
        parts = []
        for messages_list in exc.message_dict.values():
            parts.extend(str(item) for item in messages_list)
        return " ".join(parts)
    if getattr(exc, "messages", None):
        return " ".join(str(item) for item in exc.messages)
    return str(exc)
