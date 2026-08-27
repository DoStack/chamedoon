from django.db import models


class User(models.Model):
    telegram_user_id = models.BigIntegerField(unique=True)
    telegram_username = models.CharField(max_length=32, null=True, blank=True)
    first_name = models.CharField(max_length=64)
    last_name = models.CharField(max_length=64, null=True, blank=True)
    is_active = models.BooleanField(default=True)
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
