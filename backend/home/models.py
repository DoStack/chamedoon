from __future__ import annotations

import re
import uuid

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
    invite_link = models.CharField(
        max_length=200,
        blank=True,
        help_text="Invite link of a private group (t.me/+hash, t.me/joinchat/hash or t.me/hash). Stored here, never in the repository.",
    )
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


class MediaBackend(models.TextChoices):
    NONE = "", "Off (photos are not stored)"
    R2 = "r2", "Cloudflare R2"


class HomeSettings(models.Model):
    """Singleton: switches and knobs for Chamedoon Home. Blank values fall back to the server environment."""

    enabled = models.BooleanField(default=True, help_text="Master switch. Off stops every step, including the cron.")
    ingest_enabled = models.BooleanField(default=True, help_text="Read the sources.")
    review_enabled = models.BooleanField(default=True, help_text="Let the AI review waiting posts.")
    media_enabled = models.BooleanField(default=True, help_text="Download and store photos.")
    vision_enabled = models.BooleanField(default=False, help_text="Let the AI read photos to fill gaps. Needs a vision model.")
    lookback_days = models.PositiveSmallIntegerField(default=10, help_text="How far back a first read goes (1 to 30).")
    review_batch = models.PositiveSmallIntegerField(default=60, help_text="Posts the AI reviews per run (1 to 100); a run also stops at its time limit.")
    media_batch = models.PositiveSmallIntegerField(default=30, help_text="Photos stored per run (1 to 60).")
    review_model = models.CharField(
        max_length=96,
        blank=True,
        help_text="OpenAI model for reviewing posts, e.g. gpt-5-mini. Blank uses HOME_REVIEW_MODEL or gpt-5-mini.",
    )
    review_workers = models.PositiveSmallIntegerField(default=8, help_text="Posts reviewed at the same time (1 to 16).")
    vision_model = models.CharField(max_length=96, blank=True, help_text="Image-capable model id, e.g. a Gemini Flash-Lite on OpenRouter.")
    media_backend = models.CharField(max_length=8, choices=MediaBackend.choices, blank=True, default="")
    r2_account_id = models.CharField("R2 account id", max_length=64, blank=True)
    r2_bucket = models.CharField("R2 bucket", max_length=64, blank=True)
    r2_public_base = models.CharField(
        "R2 public URL", max_length=200, blank=True, help_text="Public address of the bucket, e.g. https://media.example.com"
    )
    r2_access_key_id = models.CharField("R2 access key id", max_length=64, blank=True)
    r2_secret_access_key = models.CharField(
        "R2 secret access key",
        max_length=128,
        blank=True,
        help_text="Create the R2 token with Object Read & Write on this bucket only. Never shown again after saving.",
    )
    bot_token = models.CharField(
        "Home bot token",
        max_length=128,
        blank=True,
        help_text="Token of the housing bot (@chamedoon_home_bot) from BotFather. It must be an admin of each housing channel. Never shown again after saving.",
    )
    bot_username = models.CharField(
        "Home bot username",
        max_length=64,
        blank=True,
        help_text="Filled by the channel check. Used for the Mini App links under each post.",
    )
    publish_enabled = models.BooleanField(default=False, help_text="Post new listings to the housing channels. Off until you have checked the previews.")
    publish_batch = models.PositiveSmallIntegerField(default=5, help_text="Listings posted per run (1 to 10).")
    publish_window_days = models.PositiveSmallIntegerField(default=3, help_text="Only post listings seen in the last N days, so a new channel is not flooded with old ads.")
    publish_daily_cap = models.PositiveSmallIntegerField(default=40, help_text="Most posts per channel in 24 hours.")
    publish_phone = models.BooleanField(
        default=True,
        help_text="Show the advertiser's phone number in the channel post (on by default since 2026-10-25). Off: only the Telegram username and the link to the original message.",
    )
    job_lock_until = models.DateTimeField(
        null=True, blank=True, help_text="Set while a housing run is in progress, so two runs never share the Telegram session."
    )
    publish_lock_until = models.DateTimeField(
        null=True, blank=True, help_text="Set while posts are going out, so two runs never post the same listing twice."
    )
    miniapp_v2 = models.BooleanField(default=False, help_text="Not wired to any page yet: only reported by /api/v1/. It opens the new Mini App for everyone once cutover ships.")
    miniapp_v2_allowlist = models.TextField(
        blank=True, help_text="Telegram user ids that get the new Mini App while it is off, one per line or comma separated."
    )
    contact_gate_on = models.BooleanField(
        default=False, help_text="Contact and source link need channel membership. Channel posts, alert messages and bot cards stop showing them. Old posts change only through the listing admin action."
    )
    snapshots_on = models.BooleanField(default=False, help_text="Write each city's snapshot file and the manifest to R2 after each run and after a submit.")
    snapshot_state = models.JSONField(default=dict, blank=True, help_text="The current and recent file of each city. Written by the snapshot writer.")
    snapshots_written_at = models.DateTimeField(null=True, blank=True, help_text="Last write with no failure.")
    snapshot_error = models.CharField(max_length=300, blank=True)
    areas_changed_at = models.DateTimeField(null=True, blank=True, help_text="An area or channel changed. Listings checked before this get linked again by the job.")
    area_backfill_after = models.BigIntegerField(default=0, help_text="Where the area backfill stopped: the last listing id it looked at.")
    maintenance_on = models.BooleanField(default=False, help_text="The new Mini App shows its maintenance screen and /api/v1/ refuses other calls.")
    maintenance_until = models.DateTimeField(null=True, blank=True, help_text="Optional: when maintenance should end. Blank hides the time.")
    alerts_enabled = models.BooleanField(
        default=False, help_text="Send «گوش به زنگ» alerts: a private message to each user whose alert matches a new listing."
    )
    alerts_daily_cap = models.PositiveSmallIntegerField(default=10, help_text="Most alert messages per user in 24 hours (1 to 50).")
    alerts_lock_until = models.DateTimeField(
        null=True, blank=True, help_text="Set while alerts are going out, so two runs never send the same alert twice."
    )
    freshness_enabled = models.BooleanField(
        default=True,
        help_text="Expire listings not seen again, and edit the channel posts of rented or reported listings. Off: neither happens (reports are still recorded; the edits go out once it is back on).",
    )
    owner_checks_enabled = models.BooleanField(
        default=False,
        help_text="Send «هنوز موجوده؟» to people who posted a listing in the Mini App. Off until you turn it on.",
    )
    expire_offer_days = models.PositiveSmallIntegerField(
        default=14, help_text="An offer not seen again (nor confirmed) for this many days expires and leaves the feeds (3 to 90)."
    )
    expire_wanted_days = models.PositiveSmallIntegerField(
        default=21, help_text="A wanted post not seen again (nor confirmed) for this many days expires (3 to 90)."
    )
    confirm_after_days = models.PositiveSmallIntegerField(
        default=7,
        help_text="Days after which the housing bot asks the person who posted a listing in the Mini App «هنوز موجوده؟» (1 to 60).",
    )
    webhook_secret = models.CharField(
        "Home bot webhook secret",
        max_length=128,
        blank=True,
        help_text="Telegram sends it in the X-Telegram-Bot-Api-Secret-Token header of every update. Blank uses HOME_WEBHOOK_SECRET. Never shown again after saving.",
    )
    bot_v2 = models.BooleanField(default=False, help_text="Housing bot v2 (Bot2 boards): free-text search, compact results, gated contact. Off: the bot answers exactly as before.")
    bot_v2_allowlist = models.TextField(blank=True, help_text="Telegram user ids that get bot v2 while it is off, one per line or comma separated.")
    channel_v2 = models.BooleanField(default=False, help_text="Channel post format v2 (Channel2 boards): price first, details in a quote. Off: posts stay exactly as they are.")
    admin_v3 = models.BooleanField(default=False, help_text="Back office v3 (Admin3 boards): AI search dashboard, search queries, bot message log.")
    ai_search_enabled = models.BooleanField(default=False, help_text="Read free-text bot searches with the AI. Off: the simple parser only.")
    ai_search_emergency_stop = models.BooleanField(default=False, help_text="Stops every AI search call at once, whatever the other settings say.")
    search_model = models.CharField(max_length=96, blank=True, help_text="Model for free-text search. Blank uses gpt-5-mini.")
    ai_search_user_cap = models.PositiveSmallIntegerField(default=30, help_text="AI searches per user per day.")
    ai_search_global_cap = models.PositiveIntegerField(default=1500, help_text="AI searches per day, all users.")
    ai_search_timeout_seconds = models.PositiveSmallIntegerField(default=6, help_text="After this many seconds the simple parser answers instead.")
    search_log_retention_days = models.PositiveSmallIntegerField(default=90, help_text="Search queries and bot messages older than this are deleted.")
    alerts_api_on = models.BooleanField(default=False, help_text="New Mini App: alerts can be managed (F15, F16). Sending stays with «alerts enabled».")
    account_api_on = models.BooleanField(default=False, help_text="New Mini App: account and inbox (F17, F21).")
    posting_api_on = models.BooleanField(default=False, help_text="New Mini App: posting and my listings (F18, F19, F20).")
    smart_posting_on = models.BooleanField(default=False, help_text="New Mini App: read a pasted post with the AI. Off: the manual form only.")
    post_daily_cap = models.PositiveSmallIntegerField(default=5, help_text="New posts per person per day from the new Mini App (1 to 50).")
    parse_daily_cap = models.PositiveSmallIntegerField(default=10, help_text="AI readings of a pasted post per person per day (1 to 50).")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "home settings"
        verbose_name_plural = "home settings"

    def __str__(self) -> str:
        return "Home settings"

    @classmethod
    def load(cls) -> "HomeSettings":
        row, _created = cls.objects.get_or_create(pk=1)
        return row


class HomeChannel(models.Model):
    """A Telegram channel that housing listings are posted to. Never the courier channel.

    A row without a city is the country aggregate and takes every listing of its country.
    A row with a city takes the listings of that metro: its city name or any of its aliases
    (suburbs, other spellings) matches the listing's city or area.
    """

    country = models.CharField(max_length=2, help_text="ISO country code of the listings it carries, e.g. DE or CA.")
    city = models.CharField(max_length=64, blank=True, default="", help_text="Blank: the country aggregate. Otherwise the metro, e.g. Toronto.")
    aliases = models.TextField(
        blank=True,
        help_text="Other names that belong to this metro, one per line or comma separated, e.g. North York, Richmond Hill.",
    )
    active_from = models.DateTimeField(
        null=True, blank=True, help_text="Listings posted to the country channel before this are skipped here: they were copied by hand when the city channel opened."
    )
    username = models.CharField(max_length=64, help_text="Public username without @.")
    chat_id = models.BigIntegerField(null=True, blank=True)
    title = models.CharField(max_length=128, blank=True)
    enabled = models.BooleanField(default=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["country", "city"]
        constraints = [models.UniqueConstraint(fields=["country", "city"], name="home_channel_country_city")]

    def __str__(self) -> str:
        return f"@{self.username} ({self.country}{' ' + self.city if self.city else ''})"

    def names(self) -> set[str]:
        """Lower-cased names that route a listing here."""
        parts = [self.city, *re.split(r"[,\n]", self.aliases or "")]
        return {part.strip().casefold() for part in parts if part.strip()}

    def target(self) -> int | str:
        return self.chat_id if self.chat_id else f"@{self.username}"


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
    OTHER = "other", "Other"


class RentalMode(models.TextChoices):
    """How the place is let, independent of what kind of place it is (`PropertyType`)."""

    LONG_TERM = "long_term", "Long-term lease"
    SUBLET = "sublet", "Sublet (Zwischenmiete, Untermiete)"
    LEASE_TRANSFER = "lease_transfer", "Lease transfer (Nachmieter)"
    SHORT_TERM = "short_term", "Short stay (nights or weeks)"
    UNKNOWN = "unknown", "Not stated"


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
    RENTED = "rented", "Rented"
    REPORTED = "reported", "Reported as a scam (waiting for admin review)"
    REMOVED = "removed", "Deleted by its owner"


class StatusPost(models.TextChoices):
    """What the channel posts of a listing were last edited to."""

    ORIGINAL = "", "Original post"
    RENTED = "rented", "Rented"
    REPORTED = "reported", "Reported"
    RESTORED = "restored", "Restored after review"


class HomeArea(models.Model):
    """A neighbourhood of a metro: what people filter by, and one point on the map. Never a listing's exact place."""

    country = models.CharField(max_length=2)
    city = models.CharField(max_length=64, help_text="The metro: the city of its channel.")
    canonical = models.CharField(max_length=64, help_text="Plain lower-case Latin name, as home.places.fold writes it.")
    fa = models.CharField("Persian name", max_length=64)
    group = models.CharField(max_length=32, blank=True, help_text="The heading in the all-areas sheet. Blank: no heading.")
    aliases = models.TextField(blank=True, help_text="Other names, comma separated: spellings, suburbs, Toronto intersections.")
    lat = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    lng = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    rank = models.PositiveSmallIntegerField(default=0, help_text="Order among areas with the same count. Lower comes first.")

    class Meta:
        ordering = ["country", "city", "rank", "canonical"]
        constraints = [models.UniqueConstraint(fields=["country", "city", "canonical"], name="home_area_country_city_canonical")]

    def __str__(self) -> str:
        return f"{self.canonical} ({self.city})"


class HomeListing(models.Model):
    """Structured housing listing extracted from one accepted post."""

    post = models.OneToOneField(HomePost, on_delete=models.CASCADE, related_name="listing")
    kind = models.CharField(max_length=16, choices=ListingKind.choices, db_index=True)
    property_type = models.CharField(
        max_length=24, choices=PropertyType.choices, default=PropertyType.OTHER, help_text="What kind of place; bedrooms are in rooms."
    )
    rental_mode = models.CharField(max_length=16, choices=RentalMode.choices, default=RentalMode.UNKNOWN, db_index=True)
    country = models.CharField(max_length=2, db_index=True)
    region = models.CharField(max_length=3, blank=True, help_text="Province or state code, e.g. ON, QC, BC.")
    city = models.CharField(max_length=64, blank=True, db_index=True)
    area = models.CharField(max_length=96, blank=True)
    home_area = models.ForeignKey(HomeArea, null=True, blank=True, on_delete=models.SET_NULL, related_name="listings")
    area_checked_at = models.DateTimeField(null=True, blank=True, help_text="When `home_area` was last worked out. Empty: the backfill still has to.")
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
    summary_en = models.CharField(max_length=500, blank=True)
    flags = models.JSONField(default=dict, blank=True, help_text="Local cues such as untermiete, wgb, kaution_text.")
    confidence = models.JSONField(default=dict, blank=True)
    needs_review = models.BooleanField(default=False, db_index=True)
    vision_at = models.DateTimeField(null=True, blank=True, help_text="When the photos were read by the AI.")
    review_note = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=ListingStatus.choices, default=ListingStatus.ACTIVE, db_index=True)
    canonical = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="duplicates")
    dedup_key = models.CharField(max_length=40, blank=True, db_index=True)
    channel = models.ForeignKey(HomeChannel, null=True, blank=True, on_delete=models.SET_NULL, related_name="listings")
    channel_message_id = models.BigIntegerField(null=True, blank=True)
    channel_message_ids = models.JSONField(default=list, blank=True)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    publish_attempts = models.PositiveSmallIntegerField(default=0)
    publish_error = models.CharField(max_length=300, blank=True)
    last_confirmed_at = models.DateTimeField(
        null=True, blank=True, help_text="Last time the owner or a user confirmed it is still available."
    )
    confirm_asked_at = models.DateTimeField(null=True, blank=True, help_text="When the owner was last asked «هنوز موجوده؟».")
    rented_at = models.DateTimeField(null=True, blank=True)
    status_post = models.CharField(
        max_length=16, choices=StatusPost.choices, blank=True, default="", help_text="Template the channel posts were edited to."
    )
    status_post_pending = models.BooleanField(
        default=False, db_index=True, help_text="The channel posts still have to be edited (Telegram asked to retry later)."
    )
    status_post_error = models.CharField(max_length=300, blank=True)
    contact_edit_queued_at = models.DateTimeField(
        null=True, blank=True, help_text="Queued by the admin action to take the contact out of its old channel posts."
    )
    contact_edit_done_at = models.DateTimeField(null=True, blank=True, help_text="Its channel posts hold no contact any more.")
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


class HomePublication(models.Model):
    """One listing posted to one city channel. The country aggregate keeps using the fields on the listing."""

    listing = models.ForeignKey(HomeListing, on_delete=models.CASCADE, related_name="publications")
    channel = models.ForeignKey(HomeChannel, on_delete=models.CASCADE, related_name="publications")
    message_ids = models.JSONField(default=list, blank=True)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["listing", "channel"], name="home_publication_once")]

    def __str__(self) -> str:
        return f"listing {self.listing_id} -> {self.channel}"


class ReportReason(models.TextChoices):
    TAKEN = "taken", "Already rented"
    PROBLEM = "problem", "Wrong or suspicious"
    AVAILABLE = "available", "Still available"
    SCAM = "scam", "Scam"
    WRONG = "wrong", "Wrong information"
    DUPLICATE = "duplicate", "Duplicate"
    OTHER = "other", "Other"


class HomeReport(models.Model):
    """A Mini App or bot user telling us a listing is gone, still there, or looks wrong.

    One per person, listing and reason. Bot users may have no Chamedoon account, so the
    Telegram id is the person: `user` is set when there is an account.
    """

    listing = models.ForeignKey(HomeListing, on_delete=models.CASCADE, related_name="reports")
    user = models.ForeignKey("users.User", null=True, blank=True, on_delete=models.CASCADE, related_name="home_reports")
    telegram_user_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    reason = models.CharField(max_length=16, choices=ReportReason.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["listing", "user", "reason"], name="home_report_once"),
            models.UniqueConstraint(
                fields=["listing", "telegram_user_id", "reason"],
                condition=models.Q(telegram_user_id__isnull=False),
                name="home_report_once_telegram",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.reason} #{self.listing_id}"


class AlertTerm(models.TextChoices):
    ANY = "any", "Any"
    LEASE = "lease", "Lease"
    SUBLET = "sublet", "Sublet or temporary (short stays too)"
    SHORT = "short", "Short stay only"


class AlertGender(models.TextChoices):
    ANY = "any", "Not given"
    FEMALE = "female", "Woman"
    MALE = "male", "Man"
    COUPLE = "couple", "Couple"


class AlertDelivery(models.TextChoices):
    NOW = "now", "As soon as found"
    DAILY = "daily", "Once a day, from 9:00 in the alert's city"


class HomeAlert(models.Model):
    """A «گوش به زنگ»: the housing bot messages the user when a new listing matches.

    Made in the bot, or copied from the user's own active "wanted" post in the Mini App
    (`wanted_listing`), so a saved request is notified exactly like an alert.
    """

    telegram_user_id = models.BigIntegerField(db_index=True)
    chat_id = models.BigIntegerField()
    user = models.ForeignKey("users.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="home_alerts")
    wanted_listing = models.OneToOneField(
        HomeListing, null=True, blank=True, on_delete=models.CASCADE, related_name="alert", help_text="The wanted post this alert was copied from."
    )
    country = models.CharField(max_length=2)
    city = models.CharField(max_length=64, help_text="Metro, e.g. Toronto. Suburbs in the city channel's aliases match too.")
    max_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True)
    property_types = models.JSONField(default=list, blank=True, help_text="Empty: any type.")
    term = models.CharField(max_length=8, choices=AlertTerm.choices, default=AlertTerm.ANY)
    area = models.CharField(
        max_length=96,
        blank=True,
        help_text="Neighbourhoods, comma or «یا» separated, Latin or Persian; Toronto intersections count as their district.",
    )
    gender = models.CharField(
        max_length=8, choices=AlertGender.choices, default=AlertGender.ANY, help_text="Who is looking: keeps women-only or men-only rooms away from the others."
    )
    min_rooms = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True, help_text="Whole homes only; unknown counts pass.")
    min_size_m2 = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Whole homes only; unknown sizes pass.")
    needs_registration = models.BooleanField(default=False, help_text="Germany: skip listings that say no Anmeldung.")
    answered = models.JSONField(
        default=list,
        blank=True,
        help_text="Questions the user answered: type, budget, area (the three setup questions), then the optional gender, rooms, term, registration. «مهم نیست» counts as an answer (no filter); a field never answered only matches listings that state it.",
    )
    complete = models.BooleanField(default=False, help_text="Every question of the setup was answered.")
    awaiting_area = models.BooleanField(default=False, help_text="The bot asked for a neighbourhood and waits for the next message.")
    active = models.BooleanField(default=True, db_index=True)
    paused_until = models.DateTimeField(null=True, blank=True, help_text="«یک هفته مکث»: no messages for this alert until then.")
    created_at = models.DateTimeField(auto_now_add=True)
    last_notified_at = models.DateTimeField(null=True, blank=True)
    search = models.JSONField(
        default=dict,
        blank=True,
        help_text="Saved from the Mini App feed: its filters (types, min, max, areas, term, furnished, bills, warm, reg, nowbs, gender, move, ...). Empty for bot alerts.",
    )
    viewed_at = models.DateTimeField(null=True, blank=True, help_text="When the user last opened this alert's listings in the Mini App («n جدید» counts from here).")
    delivery = models.CharField(max_length=8, choices=AlertDelivery.choices, default=AlertDelivery.NOW)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"alert {self.telegram_user_id} {self.country} {self.city}"


class AlertSendStatus(models.TextChoices):
    SENT = "sent", "Sent"
    BLOCKED = "blocked", "User blocked the bot"
    FAILED = "failed", "Failed"


class HomeAlertSent(models.Model):
    """One listing messaged to one user. A user hears about a listing once, whatever alerts matched."""

    telegram_user_id = models.BigIntegerField(db_index=True)
    listing = models.ForeignKey(HomeListing, on_delete=models.CASCADE, related_name="alert_messages")
    alert = models.ForeignKey(HomeAlert, null=True, blank=True, on_delete=models.SET_NULL, related_name="messages")
    status = models.CharField(max_length=8, choices=AlertSendStatus.choices, default=AlertSendStatus.SENT)
    message_id = models.BigIntegerField(null=True, blank=True)
    error = models.CharField(max_length=200, blank=True)
    sent_at = models.DateTimeField(auto_now_add=True, db_index=True)
    seen_at = models.DateTimeField(null=True, blank=True)
    feedback = models.CharField(max_length=4, blank=True, help_text="up (👍) or down (👎) from the match message.")
    feedback_reason = models.CharField(
        max_length=8, blank=True, help_text="Why 👎: price, area, type or stale (the listing was old or already rented)."
    )
    feedback_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-sent_at"]
        constraints = [models.UniqueConstraint(fields=["telegram_user_id", "listing"], name="home_alert_sent_once")]

    def __str__(self) -> str:
        return f"{self.telegram_user_id} <- listing {self.listing_id}"


class HomeChatState(models.Model):
    """What the housing bot waits for from a user in a free-text message (a typed budget, neighbourhoods), and their city."""

    telegram_user_id = models.BigIntegerField(unique=True)
    waiting = models.CharField(max_length=16, blank=True)
    data = models.JSONField(default=dict, blank=True)
    channel = models.ForeignKey(
        HomeChannel, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", help_text="The user's city, picked once and remembered."
    )
    locale = models.CharField(max_length=2, blank=True, default="", help_text="Bot v2 language: fa or en. Blank is fa.")
    search = models.JSONField(default=dict, blank=True, help_text="Bot v2: the current search, so its buttons survive a restart.")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.telegram_user_id} waits for {self.waiting or 'nothing'}"


class HomeSafetySeen(models.Model):
    """A person was shown the full scam warning once; afterwards a short line is enough."""

    telegram_user_id = models.BigIntegerField(unique=True)
    seen_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.telegram_user_id} saw the scam warning"


class HomeTheme(models.TextChoices):
    AUTO = "auto", "Follow Telegram"
    LIGHT = "light", "Light"
    DARK = "dark", "Dark"


class HomeLocale(models.TextChoices):
    FA = "fa", "Persian"
    EN = "en", "English"
    DE = "de", "German"


class HomeUserPrefs(models.Model):
    """What a person chose in the new Mini App. Browsing needs no row: it exists after the first save."""

    user = models.OneToOneField("users.User", on_delete=models.CASCADE, related_name="home_prefs")
    country = models.CharField(max_length=2, blank=True)
    city = models.CharField(max_length=64, blank=True)
    locale = models.CharField(max_length=2, choices=HomeLocale.choices, default=HomeLocale.FA)
    theme = models.CharField(max_length=8, choices=HomeTheme.choices, default=HomeTheme.AUTO)
    notify_enabled = models.BooleanField(default=True, help_text="Master switch. Off keeps the other switches as they are.")
    notify_alerts = models.BooleanField(default=True)
    notify_saved = models.BooleanField(default=True)
    notify_news = models.BooleanField(default=False)
    quiet_from = models.TimeField(null=True, blank=True)
    quiet_to = models.TimeField(null=True, blank=True)
    first_src = models.CharField(max_length=24, blank=True, help_text="The first link tag this person came from. Never overwritten.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"prefs of {self.user_id}"


class MembershipStatus(models.TextChoices):
    MEMBER = "member", "Member"
    NOT_MEMBER = "not_member", "Not a member"
    BANNED = "banned", "Banned from the channel"


class ChannelMembership(models.Model):
    """The last getChatMember answer for a person and a channel. A member answer is trusted for 24 hours."""

    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="home_memberships")
    channel = models.ForeignKey(HomeChannel, on_delete=models.CASCADE, related_name="memberships")
    status = models.CharField(max_length=12, choices=MembershipStatus.choices)
    checked_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "channel"], name="home_membership_user_channel")]

    def __str__(self) -> str:
        return f"{self.user_id} {self.status} in {self.channel_id}"


class ContactReveal(models.Model):
    """One person saw one listing's contact on one day. Unique, so a retry never counts twice."""

    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="home_reveals")
    listing = models.ForeignKey(HomeListing, on_delete=models.CASCADE, related_name="reveals")
    day = models.DateField()
    failed_open = models.BooleanField(default=False, help_text="Shown because Telegram could not answer the membership check.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "listing", "day"], name="home_reveal_user_listing_day")]
        indexes = [models.Index(fields=["user", "day"], name="home_reveal_user_day")]

    def __str__(self) -> str:
        return f"{self.user_id} saw {self.listing_id} on {self.day}"


class HomeCityWaitlist(models.Model):
    """A person asked for a city that has no channel yet. Unique per person and name, so a retry is safe."""

    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="home_waitlist")
    country = models.CharField(max_length=2, blank=True)
    city_name = models.CharField(max_length=64)
    city_key = models.CharField(max_length=64, help_text="The name folded for comparison.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "country", "city_key"], name="home_waitlist_user_city")]

    def __str__(self) -> str:
        return f"{self.user_id} wants {self.city_name}"


class HomeSaved(models.Model):
    """A listing a person marked to keep («نشان‌شده»), with an optional note. Unique, so marking twice is safe."""

    NOTE_LIMIT = 200

    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="home_saved")
    listing = models.ForeignKey(HomeListing, on_delete=models.CASCADE, related_name="saved_by")
    note = models.CharField(max_length=NOTE_LIMIT, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["user", "listing"], name="home_saved_user_listing")]

    def __str__(self) -> str:
        return f"{self.user_id} saved {self.listing_id}"


class SearchQueryStatus(models.TextChoices):
    OK = "ok", "OK"
    ZERO = "zero", "Zero results"
    LOW = "low", "Low confidence"
    FALLBACK = "fallback", "Timeout fallback"
    CAPPED = "capped", "Daily cap reached"


class HomeSearchQuery(models.Model):
    """One free-text search typed to the housing bot, and what the parser made of it."""

    telegram_user_id = models.BigIntegerField(db_index=True)
    raw_text = models.TextField()
    country = models.CharField(max_length=2, blank=True)
    city = models.CharField(max_length=64, blank=True)
    parser = models.CharField(max_length=96, blank=True, help_text="The model that answered, or regex.")
    confidence = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True)
    field_confidence = models.JSONField(default=dict, blank=True)
    parsed = models.JSONField(default=dict, blank=True)
    results_count = models.PositiveIntegerField(null=True, blank=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    cost_usd = models.DecimalField(max_digits=8, decimal_places=5, null=True, blank=True)
    status = models.CharField(max_length=8, choices=SearchQueryStatus.choices, default=SearchQueryStatus.OK, db_index=True)
    clicked_listing_ids = models.JSONField(default=list, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name_plural = "home search queries"

    def __str__(self) -> str:
        return self.raw_text[:60]


class BotMessageDirection(models.TextChoices):
    IN = "in", "in"
    OUT = "out", "out"


class HomeBotMessage(models.Model):
    """One line of the housing bot's conversation, for the back office. Deleted by /forget and after the retention days."""

    telegram_user_id = models.BigIntegerField(db_index=True)
    direction = models.CharField(max_length=3, choices=BotMessageDirection.choices)
    kind = models.CharField(max_length=16, db_index=True, help_text="text, command, callback, results, clarify, menu or other.")
    text = models.CharField(max_length=500, blank=True)
    query = models.ForeignKey(HomeSearchQuery, null=True, blank=True, on_delete=models.SET_NULL, related_name="messages")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.telegram_user_id} {self.direction} {self.kind}"


class NotificationKind(models.TextChoices):
    ALERT_MATCH = "alert_match", "New listings for an alert"
    LISTING_LIVE = "listing_live", "Own listing is live"
    LISTING_HELD = "listing_held", "Own listing waits for review"
    LISTING_REJECTED = "listing_rejected", "Own listing was rejected"
    LISTING_EXPIRED = "listing_expired", "Own listing closed unconfirmed"
    SAVED_CLOSED = "saved_closed", "A saved listing closed"


class HomeNotification(models.Model):
    """One line of the F17 inbox. Kept for 90 days. Holds no contact."""

    RETENTION_DAYS = 90

    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="home_inbox")
    kind = models.CharField(max_length=20, choices=NotificationKind.choices)
    listing = models.ForeignKey(HomeListing, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    alert = models.ForeignKey(HomeAlert, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    title = models.CharField(max_length=160, blank=True)
    match_count = models.PositiveIntegerField(null=True, blank=True)
    reject_code = models.CharField(max_length=32, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["user", "-created_at"], name="home_notif_user_created"),
            models.Index(fields=["created_at"], name="home_notif_created"),
        ]

    def __str__(self) -> str:
        return f"{self.kind} for {self.user_id}"


class UploadStatus(models.TextChoices):
    PENDING = "pending", "Waiting for the file"
    READY = "ready", "Checked and resized"
    POSTED = "posted", "Attached to a post"


class HomeUpload(models.Model):
    """One photo sent straight to storage, from the signed URL until it joins a post. Unused rows and files are removed."""

    TTL_HOURS = 24

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="home_uploads")
    status = models.CharField(max_length=8, choices=UploadStatus.choices, default=UploadStatus.PENDING, db_index=True)
    content_type = models.CharField(max_length=64)
    size = models.PositiveIntegerField()
    raw_key = models.CharField(max_length=128)
    full_key = models.CharField(max_length=128, blank=True)
    thumb_key = models.CharField(max_length=128, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    file_size = models.PositiveIntegerField(null=True, blank=True)
    phash = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self) -> str:
        return f"upload {self.pk} by {self.user_id}"


class HomePostKey(models.Model):
    """One `Idempotency-Key` per person: the same key again answers the first result and posts nothing new."""

    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="home_post_keys")
    key = models.CharField(max_length=64)
    listing = models.ForeignKey(HomeListing, on_delete=models.CASCADE, related_name="+")
    photos_stored = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "key"], name="home_post_key_user_key")]

    def __str__(self) -> str:
        return f"{self.user_id} {self.key}"


class HomeListingEdit(models.Model):
    """An owner's edit that review is holding. The listing keeps showing the old version until an admin approves this."""

    listing = models.OneToOneField(HomeListing, on_delete=models.CASCADE, related_name="pending_edit")
    form = models.JSONField(default=dict, help_text="The review data of the new version.")
    text = models.TextField(blank=True, help_text="The new description.")
    photo_ids = models.JSONField(default=list, blank=True)
    review_note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"edit of listing {self.listing_id}"


class HomeParseUse(models.Model):
    """How many pasted posts one person had read by the AI on one day."""

    user = models.ForeignKey("users.User", on_delete=models.CASCADE, related_name="home_parse_uses")
    day = models.DateField()
    count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "day"], name="home_parse_use_user_day")]

    def __str__(self) -> str:
        return f"{self.user_id} read {self.count} on {self.day}"
