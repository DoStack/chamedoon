"""F08's own tables. The switch, caps and query log are in `home` (HomeSettings, HomeSearchQuery), shared with the bot.

Plain rows and single-statement updates only, so all of it is safe behind PgBouncer in transaction mode.
"""

from __future__ import annotations

from django.db import models


class SearchCache(models.Model):
    """One answer per normalized query and metro. Survives across serverless instances, where the process cache does not."""

    key = models.CharField(max_length=64, unique=True)
    payload = models.JSONField()
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)


class SearchDailyCounter(models.Model):
    """Model calls of one day: scope is `global` or `user:<telegram id>`. Raised by a conditional UPDATE, so a cap holds under races."""

    scope = models.CharField(max_length=32)
    day = models.DateField(db_index=True)
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["scope", "day"], name="aisearch_counter_scope_day")]


class SearchRecentsMark(models.Model):
    """F08#11: a person's recents start after `cleared_at`. The log itself is left alone, and the undo steps back to `previous`."""

    telegram_user_id = models.BigIntegerField(unique=True)
    cleared_at = models.DateTimeField(null=True, blank=True)
    previous = models.DateTimeField(null=True, blank=True)
