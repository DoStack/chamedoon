from __future__ import annotations

from django.db import models


class SourceKind(models.TextChoices):
    CHANNEL = "channel", "Channel"
    GROUP = "group", "Group"


class HomeSource(models.Model):
    """A Telegram channel or group we read housing posts from."""

    username = models.CharField(max_length=64, unique=True)
    title = models.CharField(max_length=128, blank=True)
    kind = models.CharField(max_length=16, choices=SourceKind.choices, default=SourceKind.CHANNEL)
    country = models.CharField(max_length=2, db_index=True)
    default_city = models.CharField(max_length=64, blank=True)
    default_region = models.CharField(max_length=3, blank=True, help_text="Province or state code, e.g. ON, QC.")
    default_currency = models.CharField(max_length=3)
    enabled = models.BooleanField(default=True)
    is_agency = models.BooleanField(default=False, help_text="Agent catalog; its listings are labelled agent.")
    fetch_photos = models.BooleanField(default=True, help_text="Download and store the photos of accepted posts.")
    newest_message_id = models.BigIntegerField(null=True, blank=True)
    oldest_message_id = models.BigIntegerField(null=True, blank=True)
    backfill_complete = models.BooleanField(default=False)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["country", "username"]

    def __str__(self) -> str:
        return f"@{self.username}"


class PostStatus(models.TextChoices):
    NEW = "new", "Waiting for review"
    ACCEPTED = "accepted", "Housing listing"
    REJECTED = "rejected", "Not a listing"
    ERROR = "error", "AI error"


class HomePost(models.Model):
    """One source message, kept as read, plus what the AI decided about it."""

    source = models.ForeignKey(HomeSource, on_delete=models.CASCADE, related_name="posts")
    telegram_message_id = models.BigIntegerField()
    posted_at = models.DateTimeField(db_index=True)
    text = models.TextField()
    author_username = models.CharField(max_length=64, blank=True)
    author_name = models.CharField(max_length=128, blank=True)
    author_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    grouped_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    source_url = models.URLField(max_length=255, blank=True)
    views = models.PositiveIntegerField(null=True, blank=True)
    has_photo = models.BooleanField(default=False)
    photo_count = models.PositiveSmallIntegerField(default=0)
    text_key = models.CharField(max_length=40, blank=True, db_index=True)
    status = models.CharField(max_length=16, choices=PostStatus.choices, default=PostStatus.NEW, db_index=True)
    reject_reason = models.CharField(max_length=64, blank=True)
    review_json = models.JSONField(null=True, blank=True)
    model = models.CharField(max_length=96, blank=True)
    error = models.CharField(max_length=255, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    ingested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-posted_at", "-telegram_message_id"]
        constraints = [
            models.UniqueConstraint(fields=["source", "telegram_message_id"], name="home_post_source_message_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.source.username}/{self.telegram_message_id}"


class ListingKind(models.TextChoices):
    OFFER = "offer", "Offer"
    WANTED = "wanted", "Wanted"


class PropertyType(models.TextChoices):
    ROOM = "room", "Room in a shared home"
    STUDIO = "studio", "Studio"
    APARTMENT = "apartment", "Apartment"
    HOUSE = "house", "House"
    BASEMENT = "basement", "Basement unit"
    SUBLET = "sublet", "Sublet"
    LEASE_TRANSFER = "lease_transfer", "Lease transfer"
    SHORT_STAY = "short_stay", "Short stay"
    OTHER = "other", "Other"


class PricePeriod(models.TextChoices):
    MONTH = "month", "Per month"
    WEEK = "week", "Per week"
    NIGHT = "night", "Per night"


class PriceBasis(models.TextChoices):
    WARM = "warm", "Warm (utilities included)"
    COLD = "cold", "Cold (utilities extra)"
    UNKNOWN = "unknown", "Not stated"


class Tri(models.TextChoices):
    YES = "yes", "Yes"
    NO = "no", "No"
    UNKNOWN = "unknown", "Not stated"


class Furnished(models.TextChoices):
    YES = "yes", "Furnished"
    PARTIAL = "partial", "Partly furnished"
    NO = "no", "Unfurnished"
    UNKNOWN = "unknown", "Not stated"


class RoommateGender(models.TextChoices):
    ANY = "any", "Any"
    FEMALE = "female", "Women only"
    MALE = "male", "Men only"
    COUPLE = "couple", "Couple"


class ListerType(models.TextChoices):
    OWNER = "owner", "Owner or tenant"
    AGENT = "agent", "Agent"
    UNKNOWN = "unknown", "Not stated"


class ListingStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    DUPLICATE = "duplicate", "Duplicate"
    EXPIRED = "expired", "Expired"


class HomeListing(models.Model):
    """Structured housing listing extracted from one accepted post."""

    post = models.OneToOneField(HomePost, on_delete=models.CASCADE, related_name="listing")
    kind = models.CharField(max_length=16, choices=ListingKind.choices, db_index=True)
    property_type = models.CharField(max_length=24, choices=PropertyType.choices, default=PropertyType.OTHER)
    country = models.CharField(max_length=2, db_index=True)
    region = models.CharField(max_length=3, blank=True, help_text="Province or state code, e.g. ON, QC, BC.")
    city = models.CharField(max_length=64, blank=True, db_index=True)
    area = models.CharField(max_length=96, blank=True)
    price_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    price_max = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, help_text="Budget ceiling of a wanted post."
    )
    price_currency = models.CharField(max_length=3, blank=True)
    price_period = models.CharField(max_length=8, choices=PricePeriod.choices, default=PricePeriod.MONTH)
    price_basis = models.CharField(max_length=8, choices=PriceBasis.choices, default=PriceBasis.UNKNOWN)
    deposit_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    deposit_months = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    size_m2 = models.DecimalField(max_digits=7, decimal_places=1, null=True, blank=True)
    rooms = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    furnished = models.CharField(max_length=8, choices=Furnished.choices, default=Furnished.UNKNOWN)
    registration = models.CharField(
        max_length=8, choices=Tri.choices, default=Tri.UNKNOWN, help_text="Anmeldung possible (Germany)."
    )
    bills_included = models.CharField(max_length=8, choices=Tri.choices, default=Tri.UNKNOWN)
    available_from = models.DateField(null=True, blank=True)
    available_to = models.DateField(null=True, blank=True)
    min_term_months = models.PositiveSmallIntegerField(null=True, blank=True)
    roommate_gender = models.CharField(max_length=8, choices=RoommateGender.choices, default=RoommateGender.ANY)
    occupants_wanted = models.PositiveSmallIntegerField(null=True, blank=True)
    lister_type = models.CharField(max_length=8, choices=ListerType.choices, default=ListerType.UNKNOWN)
    contact_telegram = models.CharField(max_length=64, blank=True)
    contact_phone = models.CharField(max_length=24, blank=True, db_index=True)
    summary_fa = models.CharField(max_length=500, blank=True)
    flags = models.JSONField(default=dict, blank=True, help_text="Local cues such as untermiete, wgb, kaution_text.")
    confidence = models.JSONField(default=dict, blank=True)
    needs_review = models.BooleanField(default=False, db_index=True)
    vision_at = models.DateTimeField(null=True, blank=True, help_text="When the photos were read by the AI.")
    review_note = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=ListingStatus.choices, default=ListingStatus.ACTIVE, db_index=True)
    canonical = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="duplicates")
    dedup_key = models.CharField(max_length=40, blank=True, db_index=True)
    first_seen = models.DateTimeField()
    last_seen = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_seen", "-id"]
        indexes = [models.Index(fields=["country", "city", "kind", "status"], name="home_listing_browse_idx")]

    def __str__(self) -> str:
        return f"{self.kind} {self.property_type} {self.city}"


class MediaStatus(models.TextChoices):
    PENDING = "pending", "Waiting"
    STORED = "stored", "Stored"
    FAILED = "failed", "Failed"
    SKIPPED = "skipped", "Skipped"


class HomeMedia(models.Model):
    """One photo of a post. An album is several rows sharing a grouped_id."""

    source = models.ForeignKey(HomeSource, on_delete=models.CASCADE, related_name="media")
    post = models.ForeignKey(HomePost, null=True, blank=True, on_delete=models.CASCADE, related_name="media")
    telegram_message_id = models.BigIntegerField()
    grouped_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    position = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=8, choices=MediaStatus.choices, default=MediaStatus.PENDING, db_index=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    file_size = models.PositiveIntegerField(null=True, blank=True)
    storage_key = models.CharField(max_length=160, blank=True)
    thumb_key = models.CharField(max_length=160, blank=True)
    phash = models.BigIntegerField(null=True, blank=True, db_index=True, help_text="64-bit perceptual hash, signed.")
    error = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["post_id", "position", "telegram_message_id"]
        constraints = [
            models.UniqueConstraint(fields=["source", "telegram_message_id"], name="home_media_source_message_uniq"),
        ]

    def __str__(self) -> str:
        return f"{self.source.username}/{self.telegram_message_id}"
