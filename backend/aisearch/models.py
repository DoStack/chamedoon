"""F08's tables. Plain rows and single-statement updates only, so all of it is safe behind PgBouncer in transaction mode."""

from __future__ import annotations

from django.db import models


class SearchSettings(models.Model):
    """The switch and the caps, edited in the admin. A new row is off, so nothing changes until someone turns it on."""

    enabled = models.BooleanField(default=False, help_text="Text search for everyone. Off: the search box only looks through listing text.")
    llm_enabled = models.BooleanField(default=False, help_text="Allow the model for what the rules leave. Off: rules only.")
    user_daily_llm = models.PositiveIntegerField(default=20, help_text="Model calls per person per day.")
    global_daily_llm = models.PositiveIntegerField(default=500, help_text="Model calls per day, everyone together.")

    class Meta:
        verbose_name = "Text search settings"
        verbose_name_plural = "Text search settings"

    def __str__(self) -> str:
        return "Text search settings"

    @classmethod
    def load(cls) -> "SearchSettings":
        row, _created = cls.objects.get_or_create(pk=1)
        return row


class SearchCache(models.Model):
    """One answer per normalized query and metro. Survives across serverless instances, where the process cache does not."""

    key = models.CharField(max_length=64, unique=True)
    payload = models.JSONField()
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)


class SearchDailyCounter(models.Model):
    """Model calls of one day: scope is `global` or `user:<id>`. Raised by a conditional UPDATE, so a cap holds under races."""

    scope = models.CharField(max_length=32)
    day = models.DateField(db_index=True)
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["scope", "day"], name="aisearch_counter_scope_day")]


class HomeSearchQuery(models.Model):
    """One text search: the recent list of F08#1 and the log behind the evaluation. Rows go after 90 days."""

    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="home_search_queries")
    text = models.CharField(max_length=200)
    normalized = models.CharField(max_length=200)
    country = models.CharField(max_length=2, blank=True)
    city = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=16)
    source = models.CharField(max_length=8)
    tokens = models.PositiveIntegerField(default=0)
    answer = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    cleared_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["user", "cleared_at", "-created_at"], name="aisearch_query_recent")]
