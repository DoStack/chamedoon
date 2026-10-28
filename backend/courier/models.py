"""Courier (باربری) route matcher: bot chat state, two-sided intros, route alerts and switches.

Separate from housing (home/) on purpose. Everything that messages a user other than the one
tapping a button (intro requests, alert messages) is behind a switch that is off by default.
"""

from __future__ import annotations

from django.db import models


class CourierSettings(models.Model):
    """Singleton. Every switch that would message users is off until Mohammad turns it on."""

    bot_chat_enabled = models.BooleanField(
        default=False,
        help_text="Answer chats in the courier bot through the Django webhook (production). Env: COURIER_BOT_CHAT_ENABLED.",
    )
    intros_enabled = models.BooleanField(
        default=False,
        help_text="Send «💬» intro requests to the other person and swap usernames on accept. Env: COURIER_INTROS_ENABLED.",
    )
    alerts_enabled = models.BooleanField(
        default=False,
        help_text="Message route alert owners when a matching traveler or package appears. Env: COURIER_ALERTS_ENABLED.",
    )
    intro_daily_limit = models.PositiveSmallIntegerField(
        default=5, help_text="Most intro requests one person sends in 24 hours (1 to 20)."
    )
    alert_daily_cap = models.PositiveSmallIntegerField(
        default=10, help_text="Most alert messages one person gets in 24 hours (1 to 50)."
    )
    prohibited_text = models.TextField(
        blank=True,
        default="",
        help_text="The «چه چیزهایی ممنوعه؟» list, one item per line. Blank: the built-in list.",
    )
    webhook_secret = models.CharField(
        "Courier bot webhook secret",
        max_length=128,
        blank=True,
        default="",
        help_text="Telegram sends it in the X-Telegram-Bot-Api-Secret-Token header of every update. Blank uses TELEGRAM_WEBHOOK_SECRET. Never shown again after saving.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "courier settings"
        verbose_name_plural = "courier settings"

    def __str__(self) -> str:
        return "Courier settings"

    @classmethod
    def load(cls) -> "CourierSettings":
        row, _created = cls.objects.get_or_create(pk=1)
        return row


class CourierChat(models.Model):
    """Where one person is in the 4-step flow (role, route, date, what and weight)."""

    telegram_user_id = models.BigIntegerField(unique=True)
    chat_id = models.BigIntegerField()
    waiting = models.CharField(max_length=16, blank=True, default="", help_text="Typed text expected: date, note, city.")
    data = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"courier chat {self.telegram_user_id}"


class IntroStatus(models.TextChoices):
    PENDING = "PENDING", "Waiting for an answer"
    ACCEPTED = "ACCEPTED", "Accepted"
    DECLINED = "DECLINED", "Declined"
    EXPIRED = "EXPIRED", "Expired"


class CourierIntro(models.Model):
    """«💬»: one person asks to be introduced to the owner of a request. Usernames swap only on accept."""

    requester = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="courier_intros_sent")
    target = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="courier_intros_received")
    target_request = models.ForeignKey(
        "item_requests.ItemRequest", on_delete=models.CASCADE, related_name="courier_intros"
    )
    requester_request = models.ForeignKey(
        "item_requests.ItemRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="courier_intros_made",
    )
    status = models.CharField(max_length=16, choices=IntroStatus.choices, default=IntroStatus.PENDING, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    answered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["requester", "target_request"], name="courier_intro_once_per_request"),
        ]

    def __str__(self) -> str:
        return f"intro {self.requester_id} -> {self.target_id} ({self.status})"


class CourierRouteAlert(models.Model):
    """«🔔 گوش به زنگ مسیر»: tell me when a matching traveler (or package) appears on this route."""

    telegram_user_id = models.BigIntegerField(db_index=True)
    chat_id = models.BigIntegerField()
    user = models.ForeignKey(
        "users.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="courier_alerts"
    )
    role = models.CharField(
        max_length=16,
        help_text="The owner's side: DEMAND (has a package, wants travelers) or SUPPLY (traveler, wants packages).",
    )
    origin_country = models.CharField(max_length=2)
    origin_city = models.SlugField(max_length=64)
    destination_country = models.CharField(max_length=2)
    destination_city = models.SlugField(max_length=64)
    date_from = models.DateField(null=True, blank=True)
    date_to = models.DateField(null=True, blank=True, help_text="Blank: any upcoming date.")
    weight_kg = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True, help_text="Package weight or free space."
    )
    active = models.BooleanField(default=True, help_text="Off = paused.")
    created_at = models.DateTimeField(auto_now_add=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.role} {self.origin_city} -> {self.destination_city}"


class CourierAlertSent(models.Model):
    """One alert message about one request. The unique pair keeps it from sending twice."""

    alert = models.ForeignKey(CourierRouteAlert, on_delete=models.CASCADE, related_name="sent")
    request = models.ForeignKey(
        "item_requests.ItemRequest", on_delete=models.CASCADE, related_name="courier_alerts_sent"
    )
    sent_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["alert", "request"], name="courier_alert_sent_once")]
