from __future__ import annotations

import re

from rest_framework import serializers

from home.feed import countries, metro_channel
from home.models import Furnished, HomeUserPrefs, ListerType, ListingKind, PriceBasis, PricePeriod, PropertyType, RentalMode, RoommateGender, Tri

_SRC = re.compile(r"^[a-z0-9-]{1,24}$")


class PublicCardSerializer(serializers.Serializer):
    """One listing in a list. Never holds a contact, a source link or a source group name."""

    id = serializers.IntegerField()
    kind = serializers.ChoiceField(choices=ListingKind.choices)
    property_type = serializers.ChoiceField(choices=PropertyType.choices)
    rental_mode = serializers.ChoiceField(choices=RentalMode.choices)
    country = serializers.CharField()
    region = serializers.CharField(allow_blank=True)
    city = serializers.CharField(allow_blank=True)
    metro = serializers.CharField(allow_blank=True, help_text="The city channel this listing routes to. Blank outside the channels' cities.")
    metro_slug = serializers.CharField(allow_blank=True)
    area = serializers.CharField(allow_blank=True)
    price_amount = serializers.FloatField(allow_null=True)
    price_max = serializers.FloatField(allow_null=True, help_text="Budget ceiling of a wanted post.")
    price_currency = serializers.CharField(allow_blank=True)
    price_period = serializers.ChoiceField(choices=PricePeriod.choices)
    price_basis = serializers.ChoiceField(choices=PriceBasis.choices)
    price_monthly = serializers.FloatField(allow_null=True, help_text="The price as a month. Filter and sort on this, never on price_amount.")
    rooms = serializers.FloatField(allow_null=True)
    size_m2 = serializers.FloatField(allow_null=True)
    furnished = serializers.ChoiceField(choices=Furnished.choices)
    bills_included = serializers.ChoiceField(choices=Tri.choices)
    registration = serializers.ChoiceField(choices=Tri.choices)
    roommate_gender = serializers.ChoiceField(choices=RoommateGender.choices)
    lister_type = serializers.ChoiceField(choices=ListerType.choices)
    term = serializers.ChoiceField(
        choices=[(value, value) for value in ("lease", "sublet", "short", "lease_transfer")], help_text="The rental mode filter reads this, not rental_mode."
    )
    occupants_wanted = serializers.IntegerField(allow_null=True)
    couples_ok = serializers.BooleanField()
    wbs_required = serializers.BooleanField()
    value_ratio = serializers.FloatField(allow_null=True, help_text="Price per m² against its neighbourhood's median, for the value sort. Null in detail and without price or size.")
    summary_fa = serializers.CharField(allow_blank=True, help_text="Links, handles and phone numbers are removed.")
    summary_en = serializers.CharField(allow_blank=True, help_text="Links, handles and phone numbers are removed.")
    available_from = serializers.DateField(allow_null=True)
    available_to = serializers.DateField(allow_null=True)
    first_seen = serializers.DateTimeField()
    last_seen = serializers.DateTimeField()
    thumb = serializers.CharField(allow_blank=True)
    photo_count = serializers.IntegerField()


class GateChannelSerializer(serializers.Serializer):
    username = serializers.CharField()
    title = serializers.CharField(allow_blank=True)
    city = serializers.CharField(allow_blank=True, help_text="Blank: the country channel.")


class PhotoSerializer(serializers.Serializer):
    url = serializers.CharField()
    thumb = serializers.CharField()


class PublicListingSerializer(PublicCardSerializer):
    """The detail screen. `id` can differ from the requested id: a merged listing shows the one it became."""

    state = serializers.ChoiceField(choices=[("active", "active"), ("closed", "closed")])
    closed_reason = serializers.ChoiceField(
        choices=[("owner", "owner"), ("users", "users"), ("expired", "expired")],
        allow_null=True,
        help_text="Who closed it. Null while active.",
    )
    can_confirm = serializers.BooleanField(help_text="Offer the availability question. me/listings/{id}/ answers for the owner too.")
    poster_count = serializers.IntegerField(allow_null=True, help_text="This advertiser's live listings. Null: no author known, so hide the row.")
    deposit_amount = serializers.FloatField(allow_null=True)
    deposit_months = serializers.FloatField(allow_null=True)
    min_term_months = serializers.IntegerField(allow_null=True)
    last_confirmed_at = serializers.DateTimeField(allow_null=True)
    photos = PhotoSerializer(many=True)
    has_telegram = serializers.BooleanField(help_text="The gate would return a handle. Never shows it.")
    has_phone = serializers.BooleanField(help_text="The gate would return a number. Never shows it.")
    has_source = serializers.BooleanField(help_text="False: hide «دیدن پست اصلی».")
    channel = GateChannelSerializer(allow_null=True, help_text="The channel the gate asks for. Null: this listing has no channel to ask for.")


class ViewerListingSerializer(serializers.Serializer):
    is_owner = serializers.BooleanField()
    review_state = serializers.ChoiceField(
        choices=[("live", "live"), ("in_review", "in_review"), ("hidden", "hidden")],
        help_text="Only an owner can get in_review or hidden; anyone else gets listing_gone.",
    )
    listing = PublicListingSerializer()


class CityRowSerializer(serializers.Serializer):
    slug = serializers.CharField()
    name = serializers.CharField()
    name_fa = serializers.CharField()
    listing_count = serializers.IntegerField()
    channel = GateChannelSerializer()


class CountryRowSerializer(serializers.Serializer):
    code = serializers.CharField()
    channel = GateChannelSerializer(allow_null=True)
    cities = CityRowSerializer(many=True)


class CitiesSerializer(serializers.Serializer):
    countries = CountryRowSerializer(many=True)


class MaintenanceSerializer(serializers.Serializer):
    on = serializers.BooleanField()
    until = serializers.DateTimeField(allow_null=True, help_text="Null: show no time.")


class MetaSerializer(serializers.Serializer):
    api_version = serializers.IntegerField()
    min_api_version = serializers.IntegerField(help_text="A bundle built for an older API shows the update sheet.")
    build_id = serializers.CharField(help_text="This deploy. A bundle with another id can offer the update sheet.")
    snapshot_schema_version = serializers.IntegerField()
    manifest_url = serializers.CharField(allow_blank=True, help_text="Where feeds/manifest.json lives. Blank: no snapshots here, use the fallback feed.")
    miniapp_v2 = serializers.BooleanField(help_text="On for everyone. The exchange answers per person.")
    contact_gate_on = serializers.BooleanField()
    snapshots_on = serializers.BooleanField()
    alerts_on = serializers.BooleanField(help_text="Alert messages are going out. False: hide alert entry points.")
    maintenance = MaintenanceSerializer()


class DeepLinkFiltersSerializer(serializers.Serializer):
    types = serializers.ListField(child=serializers.ChoiceField(choices=PropertyType.choices))
    min = serializers.IntegerField(allow_null=True)
    max = serializers.IntegerField(allow_null=True)


class DeepLinkSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(
        choices=[(value, value) for value in ("landing", "country", "city", "listing", "legacy")],
        help_text="landing: a normal open. legacy: go to legacy_path, a template page.",
    )
    malformed = serializers.BooleanField(help_text="The link could not be read, so this is a normal open.")
    country = serializers.CharField(allow_blank=True)
    city = serializers.CharField(allow_blank=True, help_text="The metro's name. From the listing when the link has one.")
    city_slug = serializers.CharField(allow_blank=True)
    city_known = serializers.BooleanField(help_text="False: no channel for this city, so choose one.")
    listing_id = serializers.IntegerField(allow_null=True, help_text="Can differ from the link: a merged listing resolves to the one it became.")
    listing_state = serializers.ChoiceField(choices=[("active", "active"), ("closed", "closed"), ("gone", "gone")], allow_null=True)
    filters = DeepLinkFiltersSerializer()
    src = serializers.CharField(allow_blank=True, help_text="Pass to the exchange as `src`, so the first one is stored once.")
    legacy_path = serializers.CharField(allow_blank=True)


class PrefsSerializer(serializers.ModelSerializer):
    city_active = serializers.SerializerMethodField(help_text="False: the saved city has no channel now, so ask again.")

    class Meta:
        model = HomeUserPrefs
        fields = (
            "country",
            "city",
            "city_active",
            "locale",
            "theme",
            "notify_enabled",
            "notify_alerts",
            "notify_saved",
            "notify_news",
            "quiet_from",
            "quiet_to",
            "first_src",
        )
        read_only_fields = ("first_src",)

    def get_city_active(self, prefs: HomeUserPrefs) -> bool:
        if not prefs.city:
            return False
        channel = metro_channel(prefs.country, prefs.city)
        return bool(channel and channel.enabled)

    def validate_country(self, value: str) -> str:
        value = (value or "").upper()
        if value and value not in countries():
            raise serializers.ValidationError("Unknown country.", code="invalid_choice")
        return value

    def validate(self, attrs: dict) -> dict:
        instance = self.instance
        quiet_from = attrs.get("quiet_from", getattr(instance, "quiet_from", None))
        quiet_to = attrs.get("quiet_to", getattr(instance, "quiet_to", None))
        if (quiet_from is None) != (quiet_to is None):
            raise serializers.ValidationError({"quiet_to": serializers.ErrorDetail("Set both ends.", code="quiet_incomplete")})
        if quiet_from is not None and quiet_from == quiet_to:
            raise serializers.ValidationError({"quiet_to": serializers.ErrorDetail("Start and end are the same.", code="quiet_same")})
        return attrs


class ExchangeRequestSerializer(serializers.Serializer):
    init_data = serializers.CharField(required=False, allow_blank=True, max_length=8192, help_text="Telegram.WebApp.initData. Blank or missing: auth_outside.")
    src = serializers.CharField(required=False, allow_blank=True, max_length=64, help_text="The link tag from the deep link. Stored once; a bad one is ignored.")


class ExchangeUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    telegram_user_id = serializers.IntegerField()
    first_name = serializers.CharField()
    telegram_username = serializers.CharField(allow_null=True)


class ExchangeSerializer(serializers.Serializer):
    access_token = serializers.CharField(help_text="Send as `Authorization: Bearer` on /me calls only. Never on /public calls: that skips the cache.")
    expires_in = serializers.IntegerField(help_text="Seconds. Exchange again on every launch.")
    user = ExchangeUserSerializer()
    prefs = PrefsSerializer()
    miniapp_v2 = serializers.BooleanField(help_text="False: this person stays on the old pages.")


class WaitlistRequestSerializer(serializers.Serializer):
    country = serializers.CharField(required=False, allow_blank=True, max_length=2)
    city_name = serializers.CharField(max_length=64)

    def validate_city_name(self, value: str) -> str:
        return value.strip()

    def validate(self, attrs: dict) -> dict:
        country = (attrs.get("country") or "").upper()
        attrs["country"] = country
        if metro_channel(country, attrs["city_name"]) is not None:
            raise serializers.ValidationError({"city_name": serializers.ErrorDetail("That city is on the list.", code="city_listed")})
        return attrs


class WaitlistSerializer(serializers.Serializer):
    created = serializers.BooleanField(help_text="False: already on the waitlist (F01#7).")
    city_name = serializers.CharField()


def clean_src(value: str | None) -> str:
    value = (value or "").strip().lower()
    return value if _SRC.match(value) else ""


class ContactSerializer(serializers.Serializer):
    telegram = serializers.CharField(allow_blank=True, help_text="Handle without @. Blank: none.")
    telegram_url = serializers.CharField(allow_blank=True)
    phone = serializers.CharField(allow_blank=True)
    source_url = serializers.CharField(allow_blank=True, help_text="The original post. Blank: none, so no «دیدن پست اصلی».")
    has_any = serializers.BooleanField(help_text="False: nothing to show (F11#17).")
    membership_checked = serializers.BooleanField(help_text="False: do not print «عضو کانال» (F11#19).")
    gate = serializers.ChoiceField(
        choices=[(value, value) for value in ("member", "failed_open", "gate_off", "owner")],
        help_text="How this contact opened. owner: the person's own listing, nothing was counted.",
    )
    reveals_left = serializers.IntegerField(help_text="New listings this person can still open in the rolling 24 hours.")


class AvailabilityRequestSerializer(serializers.Serializer):
    answer = serializers.ChoiceField(choices=[("taken", "taken"), ("available", "available")])


class AvailabilitySerializer(serializers.Serializer):
    outcome = serializers.ChoiceField(
        choices=[(value, value) for value in ("noted", "rented", "confirmed", "closed")],
        help_text="noted: counted, listing unchanged. rented: this answer closed it. confirmed: marked available. closed: it was closed already.",
    )


class FeedQuerySerializer(serializers.Serializer):
    """The same filters and rules as the client runs over a snapshot: see testdata/filter_vectors.json."""

    country = serializers.CharField()
    city = serializers.CharField(help_text="Metro name or slug.")
    kind = serializers.ChoiceField(choices=ListingKind.choices, required=False)
    types = serializers.ListField(child=serializers.CharField(), required=False, help_text="property_type values, plus bed1, bed2, bed3.")
    min = serializers.IntegerField(required=False, min_value=0, help_text="Monthly value.")
    max = serializers.IntegerField(required=False, min_value=0, help_text="Monthly value.")
    unpriced = serializers.BooleanField(required=False, default=False)
    areas = serializers.ListField(child=serializers.CharField(), required=False)
    term = serializers.ChoiceField(choices=[(value, value) for value in ("lease", "sublet", "short", "lease_transfer")], required=False)
    q = serializers.CharField(required=False, allow_blank=True, max_length=80)
    gender = serializers.ChoiceField(choices=[(value, value) for value in ("female", "male", "couple")], required=False)
    move = serializers.DateField(required=False)
    by = serializers.DateField(required=False)
    furnished = serializers.BooleanField(required=False, default=False)
    owner = serializers.BooleanField(required=False, default=False)
    bills = serializers.BooleanField(required=False, default=False)
    warm = serializers.BooleanField(required=False, default=False)
    reg = serializers.BooleanField(required=False, default=False)
    nowbs = serializers.BooleanField(required=False, default=False)
    sort = serializers.ChoiceField(choices=[(value, value) for value in ("new", "value", "cheap", "dear", "date")], required=False)
    cursor = serializers.RegexField(r"^\d{1,6}$", required=False, help_text="Opaque: send back next_cursor as it came.")


class FeedSerializer(serializers.Serializer):
    generated_at = serializers.DateTimeField()
    results = PublicCardSerializer(many=True)
    total = serializers.IntegerField()
    next_cursor = serializers.CharField(allow_null=True)


class SnapshotFileSerializer(serializers.Serializer):
    schema_version = serializers.IntegerField()
    country = serializers.CharField()
    city = serializers.CharField()
    city_slug = serializers.CharField()
    version = serializers.CharField()
    generated_at = serializers.DateTimeField()
    listings = PublicCardSerializer(many=True)


class ManifestCitySerializer(serializers.Serializer):
    country = serializers.CharField()
    city = serializers.CharField()
    city_slug = serializers.CharField()
    version = serializers.CharField(help_text="Part of the file name. A new version means a new file.")
    url = serializers.CharField()
    listing_count = serializers.IntegerField()
    bytes = serializers.IntegerField(help_text="Compressed size on the wire.")


class ManifestSerializer(serializers.Serializer):
    schema_version = serializers.IntegerField(help_text="Newer than this bundle knows: show the update sheet.")
    generated_at = serializers.DateTimeField(help_text="Old: warn that the list is stale.")
    alerts_on = serializers.BooleanField()
    maintenance = MaintenanceSerializer()
    cities = ManifestCitySerializer(many=True)

