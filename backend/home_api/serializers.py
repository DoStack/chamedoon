from __future__ import annotations

import re

from rest_framework import serializers

from home.feed import countries, metro_channel
from home.models import Furnished, HomeSaved, HomeUserPrefs, ListerType, ListingKind, PriceBasis, PricePeriod, PropertyType, RentalMode, RoommateGender, Tri

SAVED_STATES = [("active", "active"), ("closed", "closed"), ("gone", "gone")]
LISTING_STATES = [("active", "active"), ("closed", "closed")]
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
    area_id = serializers.IntegerField(allow_null=True, help_text="The HomeArea this falls in. Null: shows only under «همه‌جا».")
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

    state = serializers.ChoiceField(choices=LISTING_STATES)
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


class FeaturesSerializer(serializers.Serializer):
    alerts = serializers.BooleanField(help_text="F15, F16: alerts can be managed here. Sending stays with `alerts_on`.")
    account = serializers.BooleanField(help_text="F17, F21: account and inbox. F22 and F23 save through me/prefs/, which is always open.")
    posting = serializers.BooleanField(help_text="F18, F19, F20.")
    smart_posting = serializers.BooleanField(help_text="False: F18 starts on the manual form.")


class MetaSerializer(serializers.Serializer):
    api_version = serializers.IntegerField()
    min_api_version = serializers.IntegerField(help_text="A bundle built for an older API shows the update sheet.")
    build_id = serializers.CharField(help_text="This deploy. A bundle with another id can offer the update sheet.")
    snapshot_schema_version = serializers.IntegerField()
    manifest_url = serializers.CharField(allow_blank=True, help_text="Same-origin path of the manifest. Blank: no snapshots here, use the fallback feed.")
    miniapp_v2 = serializers.BooleanField(help_text="On for everyone. The exchange answers per person.")
    contact_gate_on = serializers.BooleanField()
    snapshots_on = serializers.BooleanField()
    alerts_on = serializers.BooleanField(help_text="Alert messages are going out. False: hide alert entry points.")
    text_search_on = serializers.BooleanField(help_text="F08, from ai_search_enabled. False: the search box only looks through listing text.")
    map_style_url = serializers.CharField(allow_blank=True, help_text="MapLibre style of the map (F09). Blank: no map here, the list only.")
    maintenance = MaintenanceSerializer()
    features = FeaturesSerializer(required=False, help_text="Which later screens are open. Missing: all off.")


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


class ExchangeQaFeaturesSerializer(FeaturesSerializer):
    map = serializers.BooleanField(help_text="F09, for this person only. The map has no public switch yet.")


class ExchangeQaSerializer(serializers.Serializer):
    features = ExchangeQaFeaturesSerializer()
    text_search_on = serializers.BooleanField()
    alerts_on = serializers.BooleanField(help_text="Opens the alert screens for this person. Whether messages go out stays with the public `alerts_on`.")


class ExchangeSerializer(serializers.Serializer):
    access_token = serializers.CharField(help_text="Send as `Authorization: Bearer` on /me calls only. Never on /public calls: that skips the cache.")
    expires_in = serializers.IntegerField(help_text="Seconds. Exchange again on every launch.")
    user = ExchangeUserSerializer()
    prefs = PrefsSerializer()
    miniapp_v2 = serializers.BooleanField(help_text="Always true on a 200: a person without the flag gets `miniapp_off`.")
    qa = ExchangeQaSerializer(required=False, help_text="Only for someone on `qa_user_ids`: what replaces the same fields of /public/meta/ for this person. Missing for everyone else.")


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
    area_ids = serializers.ListField(child=serializers.IntegerField(), required=False, max_length=6, help_text="At most 6 (F07#7).")
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


class CityQuerySerializer(serializers.Serializer):
    country = serializers.CharField()
    city = serializers.CharField(help_text="Metro name or slug.")


class AreaSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    canonical = serializers.CharField()
    name_fa = serializers.CharField()
    group = serializers.CharField(allow_blank=True, help_text="Heading in the all-areas sheet. Only Toronto has them.")
    lat = serializers.FloatField(allow_null=True, help_text="One rough point for the whole area, never a listing's place.")
    lng = serializers.FloatField(allow_null=True)
    listing_count = serializers.IntegerField()
    week_count = serializers.IntegerField(help_text="Seen in the last 7 days. The order of `areas`.")


class CityAreasSerializer(serializers.Serializer):
    country = serializers.CharField()
    city = serializers.CharField()
    areas = AreaSerializer(many=True)
    no_area_count = serializers.IntegerField(help_text="Offers linked to no area, which show only under «همه‌جا».")


class AreaSearchQuerySerializer(serializers.Serializer):
    q = serializers.CharField(min_length=2, max_length=80)
    country = serializers.CharField(required=False, allow_blank=True, help_text="The city being browsed: splits `here` from `elsewhere`.")
    city = serializers.CharField(required=False, allow_blank=True)


class AreaHitSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name_fa = serializers.CharField()
    matched = serializers.CharField(help_text="The name that matched, for the second line (F07#3).")
    country = serializers.CharField()
    city = serializers.CharField()
    city_slug = serializers.CharField()


class AreaSearchSerializer(serializers.Serializer):
    here = AreaHitSerializer(many=True)
    elsewhere = AreaHitSerializer(many=True, help_text="F07#4: these offer «تغییر شهر».")


class SavedRequestSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, max_length=HomeSaved.NOTE_LIMIT, help_text="Sent: replaces the note, and blank removes it (F14#9). Not sent: the note stays.")


class SavedRowSerializer(serializers.Serializer):
    listing_id = serializers.IntegerField(help_text="The id to send to PUT and DELETE. A merged listing's `card.id` can differ.")
    state = serializers.ChoiceField(choices=SAVED_STATES, help_text="gone: F14#8, offer only «برداشتن نشان».")
    card = PublicCardSerializer(allow_null=True, help_text="Null when gone.")
    closed_reason = serializers.CharField(allow_null=True)
    note = serializers.CharField(allow_blank=True)
    saved_at = serializers.DateTimeField()


class SavedListSerializer(serializers.Serializer):
    results = SavedRowSerializer(many=True)
    total = serializers.IntegerField()
    next_cursor = serializers.CharField(allow_null=True)


class SavedIdsSerializer(serializers.Serializer):
    listing_ids = serializers.ListField(child=serializers.IntegerField(), help_text="Fills the bookmark on cards. Includes both ids of a merged listing.")


class SavedQuerySerializer(serializers.Serializer):
    cursor = serializers.RegexField(r"^\d{1,6}$", required=False)


class AlertFiltersSerializer(serializers.Serializer):
    """The feed's filters as an alert keeps them. A map box is never saved (F15#9)."""

    types = serializers.ListField(child=serializers.CharField(), required=False)
    min = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    max = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    unpriced = serializers.BooleanField(required=False)
    areas = serializers.ListField(child=serializers.CharField(), required=False)
    area_ids = serializers.ListField(child=serializers.IntegerField(), required=False, max_length=6)
    term = serializers.ChoiceField(choices=[(value, value) for value in ("lease", "sublet", "short", "lease_transfer")], required=False, allow_blank=True)
    gender = serializers.ChoiceField(choices=[(value, value) for value in ("female", "male", "couple")], required=False, allow_blank=True)
    move = serializers.DateField(required=False, allow_null=True)
    by = serializers.DateField(required=False, allow_null=True)
    furnished = serializers.BooleanField(required=False)
    owner = serializers.BooleanField(required=False)
    bills = serializers.BooleanField(required=False)
    warm = serializers.BooleanField(required=False)
    reg = serializers.BooleanField(required=False)
    nowbs = serializers.BooleanField(required=False)


ALERT_DELIVERY = [("now", "now"), ("daily", "daily")]


class AlertRowSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    country = serializers.CharField()
    city = serializers.CharField()
    city_slug = serializers.CharField()
    city_active = serializers.BooleanField(help_text="False: F16#11, the city left. Offer only delete.")
    filters = AlertFiltersSerializer()
    delivery = serializers.ChoiceField(choices=ALERT_DELIVERY)
    alert_state = serializers.ChoiceField(choices=[("active", "active"), ("paused", "paused"), ("off", "off")], help_text="paused and off offer no delivery choice (F16).")
    paused_until = serializers.DateTimeField(allow_null=True)
    origin = serializers.ChoiceField(choices=[("app", "app"), ("bot", "bot"), ("wanted", "wanted")], help_text="wanted: F16#11, not editable and outside the cap.")
    wanted_listing_id = serializers.IntegerField(allow_null=True)
    new_count = serializers.IntegerField(help_text="«[N] جدید»: matched since it was last viewed. 0 unless active.")
    created_at = serializers.DateTimeField()


class AlertListSerializer(serializers.Serializer):
    results = AlertRowSerializer(many=True)
    active_count = serializers.IntegerField(help_text="Counts against `cap`; alerts from a wanted post do not.")
    cap = serializers.IntegerField()
    bot_open = serializers.BooleanField(help_text="False: the person never opened the bot (F15#3).")
    bot_blocked = serializers.BooleanField(help_text="True: the last send got a 403 (F16#13, F23#4).")
    bot_url = serializers.CharField()
    notify_enabled = serializers.BooleanField(help_text="False: F23#3, offer to turn notifications on.")


class AlertCreateRequestSerializer(serializers.Serializer):
    country = serializers.CharField(max_length=2)
    city = serializers.CharField(max_length=64, help_text="Metro name or slug.")
    filters = AlertFiltersSerializer(required=False)
    delivery = serializers.ChoiceField(choices=ALERT_DELIVERY, required=False, default="now")


class AlertSaveSerializer(serializers.Serializer):
    save_outcome = serializers.ChoiceField(
        choices=[("created", "created"), ("duplicate", "duplicate"), ("reactivated", "reactivated")],
        help_text="created: F15#2. duplicate: F15#5, it already exists and is on. reactivated: F15#8, the same one was off or paused.",
    )
    alert = AlertRowSerializer()
    bot_open = serializers.BooleanField(help_text="False: follow with F15#3. Saved either way.")
    bot_url = serializers.CharField()


class AlertUpdateRequestSerializer(serializers.Serializer):
    filters = AlertFiltersSerializer(required=False)
    delivery = serializers.ChoiceField(choices=ALERT_DELIVERY, required=False)
    alert_state = serializers.ChoiceField(
        choices=[("active", "active"), ("paused", "paused"), ("off", "off")],
        required=False,
        help_text="paused: for 7 days. Undo of a pause sends active.",
    )


class AlertListingsSerializer(serializers.Serializer):
    results = PublicCardSerializer(many=True)
    total = serializers.IntegerField()
    next_cursor = serializers.CharField(allow_null=True)


class AccountCountsSerializer(serializers.Serializer):
    saved = serializers.IntegerField()
    listings = serializers.IntegerField()
    alerts_active = serializers.IntegerField()
    alerts_new = serializers.IntegerField(help_text="The saved tab's red badge: new alert listings only.")
    unread = serializers.IntegerField(help_text="The bell's count: every notification.")


class AccountSerializer(serializers.Serializer):
    user = ExchangeUserSerializer(help_text="`telegram_username` null: F21#8.")
    prefs = PrefsSerializer()
    counts = AccountCountsSerializer()
    channel = GateChannelSerializer(allow_null=True, help_text="F21#5: the chosen city's channel. Null: no city or no channel.")
    bot_open = serializers.BooleanField()
    bot_blocked = serializers.BooleanField()
    bot_url = serializers.CharField()


class NotificationRowSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    notification_kind = serializers.ChoiceField(
        choices=[(value, value) for value in ("alert_match", "listing_live", "listing_held", "listing_rejected", "listing_expired", "saved_closed")],
        help_text="The client writes the text from this and the fields below.",
    )
    listing_id = serializers.IntegerField(allow_null=True)
    alert_id = serializers.IntegerField(allow_null=True)
    title = serializers.CharField(allow_blank=True, help_text="What it is about, for example a listing's type and area. Holds no contact.")
    match_count = serializers.IntegerField(allow_null=True, help_text="For alert_match.")
    reject_code = serializers.CharField(allow_null=True, help_text="For listing_rejected: same names as a post's field codes.")
    target_state = serializers.ChoiceField(choices=SAVED_STATES, help_text="gone: F02#4, no destination left.")
    created_at = serializers.DateTimeField()
    read_at = serializers.DateTimeField(allow_null=True)


class NotificationListSerializer(serializers.Serializer):
    results = NotificationRowSerializer(many=True)
    unread = serializers.IntegerField()
    next_cursor = serializers.CharField(allow_null=True)


class NotificationReadRequestSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField(), required=False, max_length=100, help_text="Not sent: all of them («خواندن همه»).")


class NotificationReadSerializer(serializers.Serializer):
    unread = serializers.IntegerField()


class PostFieldsSerializer(serializers.Serializer):
    """The form of F18 and F19. All optional here: a post answers `validation_failed` with each missing field."""

    kind = serializers.ChoiceField(choices=ListingKind.choices, required=False)
    property_type = serializers.ChoiceField(choices=PropertyType.choices, required=False)
    wanted_types = serializers.ListField(child=serializers.ChoiceField(choices=PropertyType.choices), required=False, help_text="F19: every type looked for. The auto alert gets all of them.")
    rental_mode = serializers.ChoiceField(choices=RentalMode.choices, required=False)
    country = serializers.CharField(required=False, max_length=2)
    city = serializers.CharField(required=False, max_length=64)
    area = serializers.CharField(required=False, allow_blank=True, max_length=96)
    area_id = serializers.IntegerField(required=False, allow_null=True)
    price = serializers.FloatField(required=False, allow_null=True, min_value=0, max_value=10_000_000, help_text="Offer: the rent. Wanted: the budget ceiling. Both require it.")
    price_period = serializers.ChoiceField(choices=PricePeriod.choices, required=False)
    price_basis = serializers.ChoiceField(choices=PriceBasis.choices, required=False, help_text="Germany: warm or cold.")
    bills_included = serializers.ChoiceField(choices=Tri.choices, required=False)
    deposit = serializers.FloatField(required=False, allow_null=True, min_value=0)
    size_m2 = serializers.FloatField(required=False, allow_null=True, min_value=0)
    rooms = serializers.FloatField(required=False, allow_null=True, min_value=0)
    furnished = serializers.ChoiceField(choices=Furnished.choices, required=False)
    registration = serializers.ChoiceField(choices=Tri.choices, required=False, help_text="Germany: «ملد».")
    available_from = serializers.DateField(required=False, allow_null=True)
    available_to = serializers.DateField(required=False, allow_null=True)
    min_term_months = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    roommate_gender = serializers.ChoiceField(choices=RoommateGender.choices, required=False)
    household = serializers.ChoiceField(choices=[(value, value) for value in ("one", "couple", "family", "two_plus")], required=False, allow_blank=True, help_text="F19: who is looking.")
    description = serializers.CharField(required=False, allow_blank=True, max_length=1500)


class PostParseRequestSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=1500)
    kind = serializers.ChoiceField(choices=ListingKind.choices)
    country = serializers.CharField(max_length=2)
    city = serializers.CharField(required=False, allow_blank=True, max_length=64)


class CityGuessSerializer(serializers.Serializer):
    country = serializers.CharField()
    city = serializers.CharField()
    known = serializers.BooleanField(help_text="False: no channel for it.")


class PostParseSerializer(serializers.Serializer):
    understood = serializers.BooleanField(help_text="False: nothing could be read from the text, so open the manual form with the text kept.")
    parsed = PostFieldsSerializer(help_text="Only what was read. Never holds a contact.")
    missing = serializers.ListField(child=serializers.CharField(), help_text="F18#11: fields a post still needs.")
    kind_guess = serializers.ChoiceField(choices=ListingKind.choices, allow_null=True, help_text="Differs from the request: F18#26.")
    city_guess = CityGuessSerializer(allow_null=True, help_text="Differs from the request: F18#27.")
    contact_in_text = serializers.BooleanField(help_text="F18#17: a phone or handle is in the text.")
    parses_left = serializers.IntegerField(help_text="Readings left today for this person.")


class PhotoUploadRequestSerializer(serializers.Serializer):
    content_type = serializers.CharField(max_length=64)
    size = serializers.IntegerField(min_value=1, help_text="Bytes after the browser's compression. Signed, so the upload must match it.")


class PhotoUploadTicketSerializer(serializers.Serializer):
    photo_id = serializers.UUIDField(help_text="Stays this person's until posted or expired. Unused uploads are removed.")
    upload_url = serializers.CharField(help_text="PUT the file here, directly to storage.")
    headers = serializers.DictField(child=serializers.CharField(), help_text="Send these exactly with the PUT.")
    expires_in = serializers.IntegerField(help_text="Seconds until `upload_url` stops working.")


class UploadedPhotoSerializer(serializers.Serializer):
    photo_id = serializers.UUIDField()
    url = serializers.CharField()
    thumb = serializers.CharField()


class PostCreateRequestSerializer(PostFieldsSerializer):
    phone = serializers.CharField(required=False, allow_blank=True, max_length=24, help_text="Needed when the person has no Telegram username. Shown only after the gate.")
    photo_ids = serializers.ListField(child=serializers.UUIDField(), required=False, max_length=6, help_text="Confirmed uploads, in order. A wanted post has none.")
    force_new = serializers.BooleanField(required=False, default=False, help_text="«آگهی من جداست»: F18#21, skips the duplicate check.")


class PostResultSerializer(serializers.Serializer):
    post_outcome = serializers.ChoiceField(choices=[("live", "live"), ("held", "held")], help_text="live: F18#32, F19#10. held: review keeps it, F18#9, F19#3.")
    listing_id = serializers.IntegerField()
    photos_stored = serializers.IntegerField(help_text="Fewer than sent: some photos expired.")


class MineRowSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kind = serializers.ChoiceField(choices=ListingKind.choices)
    owner_state = serializers.ChoiceField(choices=[(value, value) for value in ("live", "in_review", "rejected", "closed")], help_text="The badge on F20's card.")
    closed_reason = serializers.ChoiceField(choices=[("owner", "owner"), ("users", "users"), ("expired", "expired")], allow_null=True)
    reject_code = serializers.CharField(allow_null=True, help_text="F20#4, F20#12: same names as a post's field codes.")
    card = PublicCardSerializer()
    edit_pending = serializers.BooleanField(help_text="An edit waits in review; the old version still shows.")
    can_edit = serializers.BooleanField()
    can_renew = serializers.BooleanField(help_text="F20#11: closed because it was not confirmed.")
    can_restore = serializers.BooleanField(help_text="«برگردان» after the owner closed it.")
    owner_check = serializers.BooleanField(help_text="F20#5, F20#10: ask this week's question. Answer through availability/.")
    updated_at = serializers.DateTimeField(help_text="Send back as `base_updated_at` when editing.")


class MineListSerializer(serializers.Serializer):
    results = MineRowSerializer(many=True)
    total = serializers.IntegerField()
    next_cursor = serializers.CharField(allow_null=True)


class MineFormSerializer(PostFieldsSerializer):
    """What the edit form opens with, for the owner only."""

    phone = serializers.CharField(allow_blank=True)
    photos = UploadedPhotoSerializer(many=True)
    updated_at = serializers.DateTimeField()


class ListingEditRequestSerializer(PostCreateRequestSerializer):
    base_updated_at = serializers.DateTimeField(help_text="`updated_at` from when the form opened. Changed since: `edit_conflict`.")


class ListingStatusRequestSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=[("close", "close"), ("restore", "restore"), ("renew", "renew")], help_text="Sending what is already true changes nothing, so a retry is safe.")


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
    url = serializers.CharField(help_text="Same-origin path, so reading it needs no CORS.")
    listing_count = serializers.IntegerField()
    bytes = serializers.IntegerField(help_text="Compressed size on the wire.")


class ManifestSerializer(serializers.Serializer):
    schema_version = serializers.IntegerField(help_text="Newer than this bundle knows: show the update sheet.")
    generated_at = serializers.DateTimeField(help_text="Old: warn that the list is stale.")
    alerts_on = serializers.BooleanField()
    maintenance = MaintenanceSerializer()
    cities = ManifestCitySerializer(many=True)

