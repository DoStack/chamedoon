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
    account = models.ForeignKey(
        "outreach.OutreachAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
        help_text="Account that claimed it (empty when the worker did not say).",
    )
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


class OutreachSettings(models.Model):
    """Singleton: outreach controls an operator changes in the admin. Blank uses the server environment."""

    enabled = models.BooleanField(
        null=True,
        blank=True,
        help_text="Build the outreach queue (cron and worker). Blank: OUTREACH_ENABLED.",
    )
    sending_enabled = models.BooleanField(
        null=True,
        blank=True,
        help_text="Hand queued messages to the sender. Blank: OUTREACH_SENDING_ENABLED.",
    )
    daily_limit = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Most messages attempted per Tehran day, all accounts together. Blank: OUTREACH_DAILY_LIMIT.",
    )
    account_daily_limit = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Default daily limit for one sending account (an account row can override it). "
        "Blank: OUTREACH_ACCOUNT_DAILY_LIMIT, else no per-account limit.",
    )
    min_gap_minutes = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Default minutes between two messages from the same account (an account row can override it). "
        "Blank: OUTREACH_MIN_GAP_MINUTES, else no gap on the server (the worker still waits between sends).",
    )
    send_start_hour = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="First sending hour, Tehran time (0 to 23). Blank: OUTREACH_SEND_START_HOUR.",
    )
    send_end_hour = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Sending stops at this hour, Tehran time (1 to 24). Blank: OUTREACH_SEND_END_HOUR.",
    )
    min_score = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Lowest match score (0 to 100) that gets a message. Blank: OUTREACH_MIN_SCORE.",
    )
    max_post_age_days = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Only demands posted in the last N days. Blank: OUTREACH_MAX_POST_AGE_DAYS.",
    )
    cooldown_days = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Do not message the same person again for N days. Blank: OUTREACH_COOLDOWN_DAYS.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "outreach settings"
        verbose_name_plural = "outreach settings"

    def __str__(self) -> str:
        return "Outreach settings"

    @classmethod
    def load(cls) -> "OutreachSettings":
        row, _created = cls.objects.get_or_create(pk=1)
        return row


class OutreachAccount(models.Model):
    """A Telegram account the worker sends from, e.g. sina or sara."""

    name = models.SlugField(max_length=32, unique=True, help_text="Name the worker sends as `account`, e.g. sina.")
    enabled = models.BooleanField(default=True, help_text="Off: claims for this account get nothing.")
    daily_limit = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Most messages per Tehran day. Blank: the default in outreach settings."
    )
    min_gap_minutes = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Minutes between two messages. Blank: the default in outreach settings."
    )
    last_sent_at = models.DateTimeField(
        null=True, blank=True, help_text="Last time a message was handed to this account."
    )
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "outreach account"
        verbose_name_plural = "outreach accounts"

    def __str__(self) -> str:
        return self.name
