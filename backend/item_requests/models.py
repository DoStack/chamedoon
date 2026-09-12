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
