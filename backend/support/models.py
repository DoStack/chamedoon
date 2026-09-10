from __future__ import annotations

from django.db import models


class TicketSubject(models.TextChoices):
    MATCH_PROBLEM = "MATCH_PROBLEM", "Match Problem"
    CANNOT_CONTACT_USER = "CANNOT_CONTACT_USER", "Cannot Contact User"
    REPORT_USER = "REPORT_USER", "Report User"
    TECHNICAL_ISSUE = "TECHNICAL_ISSUE", "Technical Issue"
    OTHER = "OTHER", "Other"


class TicketStatus(models.TextChoices):
    OPEN = "OPEN", "Open"
    IN_QUEUE = "IN_QUEUE", "In Queue"
    CLOSED = "CLOSED", "Closed"


class ClosedBy(models.TextChoices):
    USER = "USER", "User"
    ADMIN = "ADMIN", "Admin"
    SYSTEM = "SYSTEM", "System"


class SenderType(models.TextChoices):
    USER = "USER", "User"
    ADMIN = "ADMIN", "Admin"
    SYSTEM = "SYSTEM", "System"


class SupportTicket(models.Model):
    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="support_tickets",
    )
    subject = models.CharField(max_length=32, choices=TicketSubject.choices)
    status = models.CharField(
        max_length=16,
        choices=TicketStatus.choices,
        default=TicketStatus.OPEN,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.CharField(max_length=16, choices=ClosedBy.choices, null=True, blank=True)
    last_admin_message_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        verbose_name = "support ticket"
        verbose_name_plural = "Support Tickets"
        indexes = [
            models.Index(fields=["status", "updated_at"]),
            models.Index(fields=["user", "updated_at"]),
        ]

    def __str__(self) -> str:
        return f"Ticket {self.pk} ({self.subject}, {self.status})"

    @property
    def is_closed(self) -> bool:
        return self.status == TicketStatus.CLOSED

    @property
    def last_activity_at(self):
        return self.updated_at


class SupportMessage(models.Model):
    ticket = models.ForeignKey(
        SupportTicket,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender_type = models.CharField(max_length=16, choices=SenderType.choices)
    sender_id = models.PositiveIntegerField(null=True, blank=True)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "support message"
        verbose_name_plural = "support messages"

    def __str__(self) -> str:
        return f"Message {self.pk} on ticket {self.ticket_id}"
