from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from item_requests.models import ItemRequest, RequestType
from matching.models import Match, MatchStatus
from support.models import ClosedBy, SenderType, SupportMessage, SupportTicket, TicketStatus, TicketSubject
from users.models import User

MESSAGE_MAX_LENGTH = 4000
AUTO_CLOSE_HOURS = 72


def tickets_for_user(user: User):
    return SupportTicket.objects.filter(user=user).prefetch_related("messages")


def create_ticket(user: User, payload: dict) -> SupportTicket:
    subject = _clean_subject(payload.get("subject"))
    text = _clean_message(payload.get("message"))
    with transaction.atomic():
        ticket = SupportTicket.objects.create(
            user=user,
            subject=subject,
            status=TicketStatus.OPEN,
        )
        SupportMessage.objects.create(
            ticket=ticket,
            sender_type=SenderType.USER,
            sender_id=user.pk,
            message=text,
        )
        ticket.save(update_fields=["updated_at"])
    return ticket


def add_user_message(ticket: SupportTicket, user: User, payload: dict) -> SupportMessage:
    _assert_owner(ticket, user)
    _assert_open_for_messages(ticket)
    text = _clean_message(payload.get("message"))
    with transaction.atomic():
        message = SupportMessage.objects.create(
            ticket=ticket,
            sender_type=SenderType.USER,
            sender_id=user.pk,
            message=text,
        )
        fields = ["updated_at"]
        if ticket.status == TicketStatus.IN_QUEUE:
            ticket.status = TicketStatus.OPEN
            fields.append("status")
        ticket.save(update_fields=fields)
    return message


def add_admin_message(ticket: SupportTicket, staff_user, payload: dict) -> SupportMessage:
    _assert_open_for_messages(ticket)
    text = _clean_message(payload.get("message"))
    now = timezone.now()
    with transaction.atomic():
        message = SupportMessage.objects.create(
            ticket=ticket,
            sender_type=SenderType.ADMIN,
            sender_id=getattr(staff_user, "pk", None),
            message=text,
        )
        ticket.status = TicketStatus.IN_QUEUE
        ticket.last_admin_message_at = now
        ticket.save(update_fields=["status", "last_admin_message_at", "updated_at"])
    from notifications.services import notify_support_reply

    notify_support_reply(ticket)
    return message


def close_ticket_by_user(ticket: SupportTicket, user: User) -> SupportTicket:
    _assert_owner(ticket, user)
    return _close_ticket(ticket, ClosedBy.USER)


def close_ticket_by_admin(ticket: SupportTicket) -> SupportTicket:
    return _close_ticket(ticket, ClosedBy.ADMIN)


def set_ticket_status(ticket: SupportTicket, status: str) -> SupportTicket:
    next_status = _clean_status(status)
    if ticket.status == TicketStatus.CLOSED and next_status != TicketStatus.CLOSED:
        raise ValidationError({"status": "Closed tickets cannot be reopened."})
    if next_status == TicketStatus.CLOSED:
        return close_ticket_by_admin(ticket)
    ticket.status = next_status
    ticket.save(update_fields=["status", "updated_at"])
    return ticket


def auto_close_stale_tickets(*, hours: int = AUTO_CLOSE_HOURS) -> int:
    cutoff = timezone.now() - timedelta(hours=hours)
    tickets = list(
        SupportTicket.objects.filter(
            status=TicketStatus.IN_QUEUE,
            last_admin_message_at__lte=cutoff,
        )
    )
    closed = 0
    for ticket in tickets:
        _close_ticket(ticket, ClosedBy.SYSTEM, notify=True)
        closed += 1
    return closed


def related_activity(user: User) -> dict:
    listings = ItemRequest.objects.filter(user=user).order_by("-created_at")
    matches = (
        Match.objects.filter(Q(demand_request__user=user) | Q(supply_request__user=user))
        .select_related("demand_request", "supply_request", "demand_request__user", "supply_request__user")
        .order_by("-updated_at")
    )
    return {
        "demands": [item for item in listings if item.type == RequestType.DEMAND],
        "supplies": [item for item in listings if item.type == RequestType.SUPPLY],
        "connected": [row for row in matches if row.status == MatchStatus.CONNECTED],
        "completed": [row for row in matches if row.status == MatchStatus.COMPLETED],
        "expired": [row for row in matches if row.status == MatchStatus.EXPIRED],
    }


def admin_sender_label(sender_id: int | None) -> str:
    if not sender_id:
        return "Admin"
    staff = get_user_model().objects.filter(pk=sender_id).first()
    if staff is None:
        return "Admin"
    return (staff.get_full_name() or staff.username or "Admin").strip()


def _close_ticket(ticket: SupportTicket, closed_by: str, *, notify: bool = False) -> SupportTicket:
    if ticket.status == TicketStatus.CLOSED:
        return ticket
    now = timezone.now()
    with transaction.atomic():
        if closed_by == ClosedBy.SYSTEM:
            SupportMessage.objects.create(
                ticket=ticket,
                sender_type=SenderType.SYSTEM,
                sender_id=None,
                message="This ticket was closed automatically after 3 days without a reply.",
            )
        ticket.status = TicketStatus.CLOSED
        ticket.closed_by = closed_by
        ticket.closed_at = now
        ticket.save(update_fields=["status", "closed_by", "closed_at", "updated_at"])
    if notify:
        from notifications.services import notify_support_auto_closed

        notify_support_auto_closed(ticket)
    return ticket


def _assert_owner(ticket: SupportTicket, user: User) -> None:
    if ticket.user_id != user.id:
        raise ValidationError({"ticket": "You can only change your own tickets."})


def _assert_open_for_messages(ticket: SupportTicket) -> None:
    if ticket.status == TicketStatus.CLOSED:
        raise ValidationError({"status": "Closed tickets cannot receive new messages."})


def _clean_subject(value) -> str:
    subject = str(value or "").strip()
    if subject not in TicketSubject.values:
        raise ValidationError({"subject": "Select a valid subject."})
    return subject


def _clean_status(value) -> str:
    status = str(value or "").strip()
    if status not in TicketStatus.values:
        raise ValidationError({"status": "Select a valid status."})
    return status


def _clean_message(value) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValidationError({"message": "This field is required."})
    if len(text) > MESSAGE_MAX_LENGTH:
        raise ValidationError({"message": "Message is too long."})
    return text
