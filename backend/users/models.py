from django.db import models
from django.utils import timezone


class User(models.Model):
    telegram_user_id = models.BigIntegerField(unique=True)
    telegram_username = models.CharField(max_length=32, null=True, blank=True)
    first_name = models.CharField(max_length=64)
    last_name = models.CharField(max_length=64, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    from_market = models.BooleanField(default=False, db_index=True)
    first_started_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_started_at = models.DateTimeField(null=True, blank=True)
    start_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        handle = f"@{self.telegram_username}" if self.telegram_username else str(self.telegram_user_id)
        return f"{self.first_name} ({handle})"

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    @property
    def started_bot(self) -> bool:
        return self.first_started_at is not None

    @property
    def origin(self) -> str:
        started = self.started_bot
        if started and self.from_market:
            return "Market + bot"
        if started:
            return "Organic"
        if self.from_market:
            return "Market extracted"
        return "Unknown"


class BotStart(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="bot_starts")
    telegram_user_id = models.BigIntegerField(db_index=True)
    payload = models.CharField(max_length=64, blank=True, default="")
    started_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"/start {self.telegram_user_id} @ {self.started_at}"
