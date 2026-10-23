from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.utils import timezone


class RequestType(models.TextChoices):
    DEMAND = "DEMAND", "Demand"
    SUPPLY = "SUPPLY", "Supply"


class RequestStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    CLOSED = "CLOSED", "Closed"
    CANCELLED = "CANCELLED", "Cancelled"
    EXPIRED = "EXPIRED", "Expired"
    COMPLETED = "COMPLETED", "Completed"


ACTIVE_REQUEST_STATUSES = (RequestStatus.ACTIVE,)
ARCHIVE_REQUEST_STATUSES = (
    RequestStatus.CLOSED,
    RequestStatus.CANCELLED,
    RequestStatus.EXPIRED,
    RequestStatus.COMPLETED,
)


class ChannelStatus(models.TextChoices):
    NOT_PUBLISHED = "NOT_PUBLISHED", "Not published"
    PUBLISHED = "PUBLISHED", "Published"
    UPDATED = "UPDATED", "Updated"
    FAILED = "FAILED", "Failed"


class Category(models.Model):
    code = models.CharField(max_length=32, unique=True)
    name_en = models.CharField(max_length=64)
    name_fa = models.CharField(max_length=64)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "code"]
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        return self.code


class Country(models.Model):
    code = models.CharField(max_length=2, unique=True)
    name_en = models.CharField(max_length=64)
    name_fa = models.CharField(max_length=64)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name_en"]
        verbose_name_plural = "countries"

    def __str__(self) -> str:
        return self.code


class City(models.Model):
    country = models.ForeignKey(Country, on_delete=models.CASCADE, related_name="cities")
    slug = models.SlugField(max_length=64)
    name_en = models.CharField(max_length=64)
    name_fa = models.CharField(max_length=64)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name_en"]
        unique_together = [("country", "slug")]
        verbose_name_plural = "cities"

    def __str__(self) -> str:
        return f"{self.name_en}, {self.country.code}"


class ItemRequest(models.Model):
    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="item_requests",
    )
    type = models.CharField(max_length=16, choices=RequestType.choices)
    origin_country = models.CharField(max_length=2)
    origin_city = models.SlugField(max_length=64)
    destination_country = models.CharField(max_length=2)
    destination_city = models.SlugField(max_length=64)
    destination_cities = models.JSONField(default=list, blank=True)
    date_from = models.DateField()
    date_to = models.DateField()
    desired_date = models.DateField(null=True, blank=True)
    flight_date = models.DateField(null=True, blank=True)
    weight_kg = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    capacity_kg = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    item_categories = models.ManyToManyField(
        Category,
        related_name="item_requests",
        blank=True,
    )
    excluded_categories = models.ManyToManyField(
        Category,
        related_name="excluded_by_requests",
        blank=True,
    )
    excluded_other_text = models.CharField(max_length=255, blank=True, default="")
    description = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=RequestStatus.choices,
        default=RequestStatus.ACTIVE,
    )
    package_sent = models.BooleanField(null=True, blank=True, default=None)
    expires_at = models.DateTimeField()
    imported = models.BooleanField(default=False, db_index=True)
    # Set at import when the dates look wrong (flight far out, past, inverted window): kept, never matched.
    unmatchable_reason = models.CharField(max_length=32, blank=True, default="")
    source_url = models.URLField(max_length=255, blank=True, default="")
    channel_message_id = models.BigIntegerField(null=True, blank=True)
    channel_published_at = models.DateTimeField(null=True, blank=True)
    channel_status = models.CharField(
        max_length=16,
        choices=ChannelStatus.choices,
        default=ChannelStatus.NOT_PUBLISHED,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["type", "status"]),
            models.Index(fields=["origin_country", "origin_city"]),
            models.Index(fields=["destination_country", "destination_city"]),
        ]
        verbose_name = "request"
        verbose_name_plural = "requests"

    def __str__(self) -> str:
        stops = [city for _country, city in self.destination_stop_pairs()]
        dest = " / ".join(stops) if stops else self.destination_city
        return f"{self.type} {self.origin_city} → {dest}"

    def destination_stop_pairs(self) -> list[tuple[str, str]]:
        seen: list[tuple[str, str]] = []
        for item in self.destination_cities or []:
            if not isinstance(item, dict):
                continue
            country = str(item.get("country") or "").upper().strip()
            city = str(item.get("city") or item.get("slug") or "").strip()
            pair = (country, city)
            if country and city and pair not in seen:
                seen.append(pair)
        primary = (self.destination_country, self.destination_city)
        if primary[0] and primary[1] and primary not in seen:
            seen.append(primary)
        return seen

    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def kg_value(self) -> Decimal | None:
        if self.type == RequestType.DEMAND:
            return self.weight_kg
        return self.capacity_kg

    def route_countries(self) -> set[str]:
        """Every country the route touches: origin, main destination and each destination stop."""
        found = {(self.origin_country or "").strip().upper(), (self.destination_country or "").strip().upper()}
        found.update(country for country, _city in self.destination_stop_pairs())
        return {country for country in found if country}


class CourierChannel(models.Model):
    """A per-country courier (باربری) channel. Gets every courier request whose route touches its country.

    Posted by the courier bot (TELEGRAM_BOT_TOKEN) with the same text and button as the main
    courier channel, in addition to it. Housing never posts here and courier never posts to a
    housing channel.
    """

    country = models.CharField(
        max_length=2,
        unique=True,
        help_text="ISO country code, e.g. CA or DE. A request goes here when its origin or a destination is in this country.",
    )
    username = models.CharField(max_length=64, blank=True, default="", help_text="Public username without @. Blank while it is not set.")
    chat_id = models.BigIntegerField(null=True, blank=True, help_text="Numeric id, e.g. -100…. Used before the username.")
    title = models.CharField(max_length=128, blank=True, default="")
    enabled = models.BooleanField(default=True)
    aliases = models.TextField(blank=True, default="", help_text="Notes or other names for this channel. Not used for routing.")
    active_from = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Only requests created after this are posted here, so a new channel is not flooded with old ones.",
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        ordering = ["country"]
        verbose_name = "courier country channel"
        verbose_name_plural = "courier country channels"

    def __str__(self) -> str:
        label = f"@{self.username}" if self.username else (self.title or str(self.chat_id or ""))
        return f"{label} ({self.country})"

    def save(self, *args, **kwargs) -> None:
        self.country = (self.country or "").strip().upper()
        self.username = (self.username or "").strip().lstrip("@")
        super().save(*args, **kwargs)

    def target(self) -> int | str | None:
        if self.chat_id:
            return self.chat_id
        return f"@{self.username}" if self.username else None


class CourierPublication(models.Model):
    """One courier request posted to one country channel. The unique pair keeps it from posting twice."""

    request = models.ForeignKey(ItemRequest, on_delete=models.CASCADE, related_name="country_publications")
    channel = models.ForeignKey(CourierChannel, on_delete=models.CASCADE, related_name="publications")
    message_ids = models.JSONField(default=list, blank=True)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True, help_text="When the post was marked unavailable.")
    claimed_at = models.DateTimeField(null=True, blank=True, help_text="A run is sending this right now.")
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["request", "channel"], name="courier_publication_once")]

    def __str__(self) -> str:
        return f"request {self.request_id} -> {self.channel}"
