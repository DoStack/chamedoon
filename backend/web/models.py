"""One-time codes of browser sign-in. Only hashes are stored: the code and the poll token never sit in the database."""

from __future__ import annotations

from django.db import models


class WebLoginStatus(models.TextChoices):
    PENDING = "pending", "Waiting for the bot"
    OPENED = "opened", "Opened in the bot"
    CONFIRMED = "confirmed", "Confirmed in the bot"
    USED = "used", "Signed in"
    DEAD = "dead", "Killed (wrong number, second person, or expired)"


class WebLoginCode(models.Model):
    """«ورود با تلگرام»: the browser starts it, the bot confirms it (right number), the browser redeems it once."""

    code_hash = models.CharField(max_length=64, unique=True)
    poll_hash = models.CharField(max_length=64, unique=True)
    match = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=10, choices=WebLoginStatus.choices, default=WebLoginStatus.PENDING, db_index=True)
    ip = models.CharField(max_length=64, blank=True, db_index=True)
    telegram_user_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    user = models.ForeignKey("users.User", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    # The email-only account that asked to connect Telegram (merged into the Telegram account on redeem).
    link_user = models.ForeignKey("users.User", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    expires_at = models.DateTimeField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class EmailLoginCode(models.Model):
    """A 6-digit code sent by email. 10 minutes, 5 tries, one use."""

    email = models.EmailField(max_length=254, db_index=True)
    code_hash = models.CharField(max_length=64)
    attempts = models.PositiveSmallIntegerField(default=0)
    ip = models.CharField(max_length=64, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    dead = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
