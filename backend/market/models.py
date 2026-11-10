from __future__ import annotations

from django.db import models

from market.poster import PosterFields


class MarketRole(models.TextChoices):
    SUPPLY = "supply", "Traveler / can carry"
    DEMAND = "demand", "Sender / needs carry"
    NOISE = "noise", "Ad or off-topic"
    UNKNOWN = "unknown", "Unclassified"


class MarketPost(PosterFields):
    channel_username = models.CharField(max_length=64, db_index=True)
    telegram_message_id = models.BigIntegerField()
    posted_at = models.DateTimeField(db_index=True)
    text = models.TextField()
    views = models.PositiveIntegerField(null=True, blank=True)
    has_photo = models.BooleanField(default=False)
    role = models.CharField(max_length=16, choices=MarketRole.choices, default=MarketRole.UNKNOWN, db_index=True)
    origin_city = models.CharField(max_length=64, blank=True)
    origin_country = models.CharField(max_length=2, blank=True, db_index=True)
    destination_city = models.CharField(max_length=64, blank=True)
    destination_country = models.CharField(max_length=2, blank=True, db_index=True)
    weight_kg = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    source_url = models.URLField(max_length=255, blank=True)
    author_name = models.CharField(max_length=128, blank=True)
    author_username = models.CharField(max_length=64, blank=True)
    review_json = models.JSONField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    item_request = models.OneToOneField(
        "item_requests.ItemRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="market_post",
    )
    skip_reason = models.CharField(max_length=64, blank=True)
    migrated_at = models.DateTimeField(null=True, blank=True)
    llm_retry_started_at = models.DateTimeField(null=True, blank=True)
    ingested_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-posted_at", "-telegram_message_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["channel_username", "telegram_message_id"],
                name="market_post_channel_message_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["channel_username", "-posted_at"], name="market_post_ch_posted_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.channel_username}/{self.telegram_message_id}"

    def route_label(self) -> str:
        origin = self.origin_city or self.origin_country
        dest = self.destination_city or self.destination_country
        if origin and dest:
            return f"{origin} → {dest}"
        return ""


class MarketIngestState(models.Model):
    channel_username = models.CharField(max_length=64, unique=True)
    newest_message_id = models.BigIntegerField(null=True, blank=True)
    oldest_message_id = models.BigIntegerField(null=True, blank=True)
    backfill_complete = models.BooleanField(default=False)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_created = models.PositiveIntegerField(default=0)
    last_updated = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    extract_cursor_id = models.BigIntegerField(null=True, blank=True)
    lookback_before_id = models.BigIntegerField(null=True, blank=True)
    lookback_days = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        verbose_name = "market ingest state"
        verbose_name_plural = "market ingest state"

    def __str__(self) -> str:
        return self.channel_username


class MarketSourceKind(models.TextChoices):
    CHANNEL = "channel", "Public channel"
    GROUP = "group", "Private group (invite)"


class MarketSource(models.Model):
    """A Telegram channel or group the courier crawl reads. While this table is empty, the server settings
    MARKET_CHANNEL_USERNAMES and MARKET_SOURCE_INVITES are used instead; the first row switches to the table."""

    username = models.CharField(
        max_length=64,
        unique=True,
        help_text="Public username without @, or a stable key for a private group (e.g. koolbarcanada-group). Also the ingest-state key.",
    )
    kind = models.CharField(max_length=16, choices=MarketSourceKind.choices, default=MarketSourceKind.CHANNEL)
    invite_hash = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Private group only: the hash of t.me/+hash or t.me/joinchat/hash (a full link is fine). The reader account must be a member.",
    )
    enabled = models.BooleanField(default=True, help_text="Read it. Off: not read, still known as a source.")
    retired = models.BooleanField(
        default=False,
        help_text="Gone from Telegram. Never read, but its name still marks a post's author as a source, not a person.",
    )
    note = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["kind", "username"]
        verbose_name = "market source"
        verbose_name_plural = "market sources"

    def __str__(self) -> str:
        return self.username

    def save(self, *args, **kwargs) -> None:
        self.username = (self.username or "").strip().lstrip("@")
        self.invite_hash = clean_invite(self.invite_hash)
        super().save(*args, **kwargs)

    @property
    def reads(self) -> bool:
        return self.enabled and not self.retired and (self.kind == MarketSourceKind.CHANNEL or bool(self.invite_hash))


def clean_invite(value: str) -> str:
    value = (value or "").strip()
    for prefix in ("https://t.me/joinchat/", "https://t.me/+", "http://t.me/+", "t.me/joinchat/", "t.me/+"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value.lstrip("+")


def source_rows() -> list[MarketSource] | None:
    """Every MarketSource row, or None while the table is empty (or missing before migrations): then settings rule."""
    from django.db import DatabaseError

    try:
        rows = list(MarketSource.objects.all())
    except DatabaseError:
        return None
    return rows or None
