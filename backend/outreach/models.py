from __future__ import annotations

from django.db import models


class OutreachStatus(models.TextChoices):
    QUEUED = "QUEUED", "Queued"
    SENDING = "SENDING", "Sending"
    SENT = "SENT", "Sent"
    FAILED = "FAILED", "Failed"
    SKIPPED = "SKIPPED", "Skipped"
    HELD = "HELD", "Held for review"


class OutreachMessage(models.Model):
    """One Telegram DM from the Chamedoon account to an imported demander."""

    recipient = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="outreach_messages",
    )
    recipient_username = models.CharField(max_length=32, db_index=True)
    demand_request = models.OneToOneField(
        "item_requests.ItemRequest",
        on_delete=models.CASCADE,
        related_name="outreach_message",
    )
    matches = models.ManyToManyField("matching.Match", related_name="outreach_messages", blank=True)
    text = models.TextField()
    token = models.CharField(max_length=32, unique=True)
    status = models.CharField(
        max_length=16,
        choices=OutreachStatus.choices,
        default=OutreachStatus.QUEUED,
        db_index=True,
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    claimed_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True, db_index=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_detail = models.CharField(max_length=500, blank=True)
    recipient_telegram_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    telegram_message_id = models.BigIntegerField(null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    opened_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    replied_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "outreach message"
        verbose_name_plural = "outreach messages"

    def __str__(self) -> str:
        return f"@{self.recipient_username} ({self.status})"


class OutreachOptOut(models.Model):
    telegram_username = models.CharField(max_length=32, blank=True, db_index=True)
    telegram_user_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    source = models.CharField(max_length=16, default="reply")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "outreach opt-out"
        verbose_name_plural = "outreach opt-outs"

    def __str__(self) -> str:
        return f"@{self.telegram_username}" if self.telegram_username else str(self.telegram_user_id)


class OutreachState(models.Model):
    """Singleton: sending pause and worker health."""

    stopped = models.BooleanField(
        default=False,
        help_text="Stop all sending right away (no redeploy needed).",
    )
    paused_until = models.DateTimeField(null=True, blank=True)
    pause_reason = models.CharField(max_length=64, blank=True)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)
    last_built_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "outreach state"
        verbose_name_plural = "outreach state"

    def __str__(self) -> str:
        return "Outreach state"

    @classmethod
    def load(cls) -> "OutreachState":
        state, _created = cls.objects.get_or_create(pk=1)
        return state
