"""The JSON of /api/v1/courier/, and the functions that shape domain rows into it.

Nothing about a person leaves here before the other side accepts: a card has no name, username, id,
source link or raw description. `Contact` is built only for an accepted intro or a contact-allowed match.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from courier import alerts as alert_domain
from courier import conf, intros, search, texts
from courier.models import CourierIntro, CourierRouteAlert, CourierUserPrefs, IntroStatus
from item_requests.models import City, ItemRequest, RequestStatus, RequestType
from item_requests.services import request_is_due
from matching.completion import rating_state
from matching.contact import clean_telegram_username, contact_for_match, telegram_dm_contact
from matching.models import Match, MatchStatus
from support.models import SupportMessage, SupportTicket, TicketSubject


def _clean_place(value: str) -> tuple[str, str] | None:
    country, _sep, city = (value or "").partition(":")
    country, city = country.strip().upper()[:2], city.strip()[:64]
    return (country, city) if len(country) == 2 and city else None


def clean_place(value: str, field: str) -> str:
    if value and _clean_place(value) is None:
        raise serializers.ValidationError({field: serializers.ErrorDetail("invalid", code="invalid")})
    return value


class Names:
    """City names for one response, read once."""

    def __init__(self) -> None:
        self._rows = {(city["country__code"], city["slug"]): city for city in City.objects.values("country__code", "slug", "name_fa", "name_en")}

    def place(self, country: str, slug: str) -> dict:
        row = self._rows.get(((country or "").upper(), slug))
        fallback = (slug or "").replace("-", " ").title()
        return {
            "country": country,
            "city": slug,
            "name_fa": row["name_fa"] if row else fallback,
            "name_en": row["name_en"] if row else fallback,
        }


def _categories(rows) -> list[dict]:
    return [{"code": row.code, "name_fa": row.name_fa, "name_en": row.name_en} for row in rows]


def _date_kind(item: ItemRequest) -> str | None:
    if search.request_date(item) is None:
        return None
    if item.type == RequestType.SUPPLY:
        return "flight"
    return "exact" if item.desired_date else "by"


def card(item: ItemRequest, names: Names, trust: dict[int, int]) -> dict:
    return {
        "id": item.pk,
        "type": item.type,
        "origin": names.place(item.origin_country, item.origin_city),
        "stops": [names.place(country, city) for country, city in item.destination_stop_pairs()],
        "date": search.request_date(item),
        "date_kind": _date_kind(item),
        "capacity_kg": item.capacity_kg,
        "weight_kg": item.weight_kg,
        "categories": _categories(item.item_categories.all()),
        "refused": _categories(item.excluded_categories.all()),
        "trust_count": trust.get(item.user_id, 0),
        "created_at": item.created_at,
    }


def cards(items: list[ItemRequest], names: Names | None = None) -> list[dict]:
    names = names or Names()
    trust = search.completed_counts({item.user_id for item in items})
    return [card(item, names, trust) for item in items]


def route_keys(item: ItemRequest) -> dict:
    """Enough for «مسافرهای همین مسیر» on a screen whose request is gone."""
    return {
        "side": search.opposite(item.type),
        "origin": f"{item.origin_country}:{item.origin_city}",
        "destination": f"{item.destination_country}:{item.destination_city}",
    }


def date_passed(item: ItemRequest) -> bool:
    day = search.request_date(item)
    return day is not None and day < timezone.localdate()


def request_detail(item: ItemRequest, viewer, names: Names) -> dict:
    trust = search.completed_counts({item.user_id})
    intro = CourierIntro.objects.filter(requester=viewer, target_request=item).first()
    mine = intros.own_request_for(viewer, item)
    if not conf.intros_enabled():
        reach = "off"
    elif intros.reachable_chat(item.user) is not None:
        reach = "intro"
    else:
        reach = "published" if search.has_way_to_reach(item) else "none"
    known = (item.user.telegram_username or "",)
    return {
        **card(item, names, trust),
        "description": search.public_text(item.description, known),
        "excluded_other_text": search.public_text(item.excluded_other_text, known),
        "reach": reach,
        "intro_id": intro.pk if intro else None,
        "intro_status": intros.live_intro_status(intro) if intro else None,
        "my_request": card(mine, names, search.completed_counts({mine.user_id})) if mine else None,
    }


def published_contact(item: ItemRequest) -> dict:
    """What the author made public themselves. Nothing else about them, and only https links reach a button."""
    handle = clean_telegram_username(item.user.telegram_username) if item.imported else ""
    url = item.source_url if (item.source_url or "").startswith("https://") else ""
    return {"handle": handle or None, "source_url": url or None}


def my_request(item: ItemRequest, names: Names, *, suggestions: int | None = None) -> dict:
    from notifications.channel import channel_enabled

    due = request_is_due(item)
    return {
        **cards([item], names)[0],
        "status": RequestStatus.EXPIRED if due else item.status,
        "description": item.description,
        "excluded_other_text": item.excluded_other_text,
        "editable": item.status == RequestStatus.ACTIVE and not due and not item.is_expired(),
        "date_passed": date_passed(item),
        "package_sent": item.package_sent,
        "channel_on": channel_enabled(),
        "channel_status": item.channel_status,
        "suggestions": suggestions,
    }


def contact(user, draft: str = "") -> dict:
    return _contact_shape(telegram_dm_contact(user, draft))


def _contact_shape(raw: dict) -> dict:
    """The Telegram id shows only when there is no username."""
    username = raw["telegram_username"]
    return {
        "first_name": raw["first_name"],
        "telegram_username": username,
        "telegram_user_id": raw["telegram_user_id"] if not username and raw["show_telegram_id"] else None,
        "url": raw["telegram_url"],
        "draft": raw["draft"],
        "no_username": not username,
    }


def _pair_match(intro: CourierIntro) -> int | None:
    if intro.requester_request_id is None:
        return None
    pair = {intro.target_request_id, intro.requester_request_id}
    match = Match.objects.filter(demand_request_id__in=pair, supply_request_id__in=pair).values_list("pk", flat=True).first()
    return match


def intro_shape(intro: CourierIntro, viewer, names: Names) -> dict:
    is_target = intro.target_id == viewer.pk
    other = intro.requester if is_target else intro.target
    status = intros.live_intro_status(intro)
    rows = cards([row for row in (intro.target_request, intro.requester_request) if row is not None], names)
    return {
        "id": intro.pk,
        "status": status,
        "role": "target" if is_target else "requester",
        "created_at": intro.created_at,
        "answered_at": intro.answered_at,
        "expires_at": intro.created_at + timedelta(hours=conf.INTRO_EXPIRE_HOURS),
        "target_request": rows[0],
        "requester_request": rows[1] if len(rows) > 1 else None,
        # The target sees who asks, as in the bot's message. The requester sees no name before acceptance.
        "requester_first_name": intro.requester.first_name if is_target else None,
        "requester_trust_count": search.completed_count(intro.requester) if is_target else None,
        "contact": contact(other) if status == IntroStatus.ACCEPTED else None,
        "match_id": _pair_match(intro) if status == IntroStatus.ACCEPTED else None,
    }


def live_match_status(match: Match) -> str:
    """The status as it reads now, without writing: a connected match on a request due to expire is expired."""
    if match.status == MatchStatus.CONNECTED and (request_is_due(match.demand_request) or request_is_due(match.supply_request)):
        return MatchStatus.EXPIRED
    return match.status


def match_shape(match: Match, viewer, names: Names) -> dict:
    mine = match.demand_request if match.role_for(viewer) == "demand" else match.supply_request
    other = match.counterpart_request(viewer)
    rows = cards([mine, other], names)
    status = live_match_status(match)
    released = intros.contact_released(match)
    raw = contact_for_match(match, viewer) if released and status != MatchStatus.EXPIRED else None
    state = rating_state(match, viewer)
    return {
        "id": match.pk,
        "status": status,
        "role": match.role_for(viewer),
        "my_request": rows[0],
        "other_request": rows[1],
        "contact": _contact_shape(raw) if raw else None,
        "rating": {
            "my_score": state["my_rating"],
            "my_comment": state["my_comment"],
            "their_score": state["their_rating"],
            "their_comment": state["their_comment"],
            "can_complete": state["can_complete"] and released and status == MatchStatus.CONNECTED,
            "can_rate": state["can_rate"],
        },
        "created_at": match.created_at,
        "updated_at": match.updated_at,
    }


def alert_shape(alert: CourierRouteAlert, names: Names) -> dict:
    return {
        "id": alert.pk,
        "side": alert.role,
        "origin": names.place(alert.origin_country, alert.origin_city),
        "destination": names.place(alert.destination_country, alert.destination_city),
        "date_from": alert.date_from,
        "date_to": alert.date_to,
        "weight_kg": alert.weight_kg,
        "active": alert.active,
        "created_at": alert.created_at,
        "last_sent_at": alert.last_sent_at,
    }


def alerts_shape(telegram_user_id: int) -> dict:
    names = Names()
    rows = list(alert_domain.user_alerts(telegram_user_id))
    return {
        "alerts": [alert_shape(row, names) for row in rows],
        "cap": conf.MAX_ALERTS_PER_USER,
        "sending_on": conf.alerts_enabled(),
    }


def prefs_shape(user) -> dict:
    row = CourierUserPrefs.objects.filter(user=user).first() or CourierUserPrefs(user=user)
    return {"side": row.side, "last_origin": row.last_origin, "last_destination": row.last_destination, "locale": row.locale, "theme": row.theme}


def public_username(value: str | None) -> str | None:
    return clean_telegram_username(value) or None


def ticket_shape(ticket: SupportTicket, *, with_messages: bool) -> dict:
    rows = list(ticket.messages.all())
    shape = {
        "id": ticket.pk,
        "subject": ticket.subject,
        "status": ticket.status,
        "created_at": ticket.created_at,
        "updated_at": ticket.updated_at,
        "closed_at": ticket.closed_at,
        "closed_by": ticket.closed_by,
        "last_message": rows[-1].message[:140] if rows else "",
    }
    if with_messages:
        shape["messages"] = [message_shape(row) for row in rows]
    return shape


def message_shape(row: SupportMessage) -> dict:
    return {"id": row.pk, "sender_type": row.sender_type, "message": row.message, "created_at": row.created_at}


# Shapes


class PlaceSerializer(serializers.Serializer):
    country = serializers.CharField()
    city = serializers.CharField()
    name_fa = serializers.CharField()
    name_en = serializers.CharField()


class CategorySerializer(serializers.Serializer):
    code = serializers.CharField()
    name_fa = serializers.CharField()
    name_en = serializers.CharField()


class RequestCardSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    type = serializers.ChoiceField(choices=RequestType.choices)
    origin = PlaceSerializer()
    stops = PlaceSerializer(many=True)
    date = serializers.DateField(allow_null=True)
    date_kind = serializers.ChoiceField(choices=["flight", "exact", "by"], allow_null=True)
    capacity_kg = serializers.DecimalField(max_digits=6, decimal_places=2, allow_null=True)
    weight_kg = serializers.DecimalField(max_digits=6, decimal_places=2, allow_null=True)
    categories = CategorySerializer(many=True)
    refused = CategorySerializer(many=True)
    trust_count = serializers.IntegerField()
    created_at = serializers.DateTimeField()


class RequestListSerializer(serializers.Serializer):
    items = RequestCardSerializer(many=True)
    total = serializers.IntegerField()
    next_offset = serializers.IntegerField(allow_null=True)


class RequestDetailSerializer(RequestCardSerializer):
    description = serializers.CharField(allow_blank=True)
    excluded_other_text = serializers.CharField(allow_blank=True)
    reach = serializers.ChoiceField(choices=["intro", "published", "none", "off"])
    intro_id = serializers.IntegerField(allow_null=True)
    intro_status = serializers.ChoiceField(choices=IntroStatus.choices, allow_null=True)
    my_request = RequestCardSerializer(allow_null=True)


class PublishedContactSerializer(serializers.Serializer):
    handle = serializers.CharField(allow_null=True)
    source_url = serializers.CharField(allow_null=True)


class MyRequestSerializer(RequestCardSerializer):
    status = serializers.ChoiceField(choices=RequestStatus.choices)
    description = serializers.CharField(allow_blank=True)
    excluded_other_text = serializers.CharField(allow_blank=True)
    editable = serializers.BooleanField()
    date_passed = serializers.BooleanField()
    package_sent = serializers.BooleanField(allow_null=True)
    channel_on = serializers.BooleanField()
    channel_status = serializers.CharField()
    suggestions = serializers.IntegerField(allow_null=True)


class MyRequestsSerializer(serializers.Serializer):
    items = MyRequestSerializer(many=True)


class CounterpartsSerializer(serializers.Serializer):
    items = RequestCardSerializer(many=True)


class FacetMonthSerializer(serializers.Serializer):
    key = serializers.CharField()
    count = serializers.IntegerField()


class FacetSizeSerializer(serializers.Serializer):
    code = serializers.CharField()
    count = serializers.IntegerField()


class FacetsSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    months = FacetMonthSerializer(many=True)
    sizes = FacetSizeSerializer(many=True)


class ContactSerializer(serializers.Serializer):
    first_name = serializers.CharField(allow_blank=True)
    telegram_username = serializers.CharField(allow_null=True)
    telegram_user_id = serializers.IntegerField(allow_null=True)
    url = serializers.CharField(allow_blank=True)
    draft = serializers.CharField(allow_blank=True)
    no_username = serializers.BooleanField()


class IntroSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    status = serializers.ChoiceField(choices=IntroStatus.choices)
    role = serializers.ChoiceField(choices=["requester", "target"])
    created_at = serializers.DateTimeField()
    answered_at = serializers.DateTimeField(allow_null=True)
    expires_at = serializers.DateTimeField()
    target_request = RequestCardSerializer()
    requester_request = RequestCardSerializer(allow_null=True)
    requester_first_name = serializers.CharField(allow_null=True)
    requester_trust_count = serializers.IntegerField(allow_null=True)
    contact = ContactSerializer(allow_null=True)
    match_id = serializers.IntegerField(allow_null=True)


class RatingStateSerializer(serializers.Serializer):
    my_score = serializers.IntegerField(allow_null=True)
    my_comment = serializers.CharField(allow_blank=True)
    their_score = serializers.IntegerField(allow_null=True)
    their_comment = serializers.CharField(allow_blank=True)
    can_complete = serializers.BooleanField()
    can_rate = serializers.BooleanField()


class MatchSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    status = serializers.ChoiceField(choices=MatchStatus.choices)
    role = serializers.ChoiceField(choices=["demand", "supply"])
    my_request = RequestCardSerializer()
    other_request = RequestCardSerializer()
    contact = ContactSerializer(allow_null=True)
    rating = RatingStateSerializer()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class InboxSerializer(serializers.Serializer):
    incoming = IntroSerializer(many=True)
    outgoing = IntroSerializer(many=True)
    accepted = IntroSerializer(many=True)
    closed = IntroSerializer(many=True)
    matches = MatchSerializer(many=True)


class AlertSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    side = serializers.ChoiceField(choices=RequestType.choices)
    origin = PlaceSerializer()
    destination = PlaceSerializer()
    date_from = serializers.DateField(allow_null=True)
    date_to = serializers.DateField(allow_null=True)
    weight_kg = serializers.DecimalField(max_digits=6, decimal_places=2, allow_null=True)
    active = serializers.BooleanField()
    created_at = serializers.DateTimeField()
    last_sent_at = serializers.DateTimeField(allow_null=True)


class AlertsSerializer(serializers.Serializer):
    alerts = AlertSerializer(many=True)
    cap = serializers.IntegerField()
    sending_on = serializers.BooleanField()


class AlertSavedSerializer(serializers.Serializer):
    alert = AlertSerializer()
    created = serializers.BooleanField()
    sending_on = serializers.BooleanField()


class PrefsSerializer(serializers.Serializer):
    side = serializers.ChoiceField(choices=[("", ""), *RequestType.choices], required=False, allow_blank=True)
    last_origin = serializers.CharField(required=False, allow_blank=True, max_length=70)
    last_destination = serializers.CharField(required=False, allow_blank=True, max_length=70)
    locale = serializers.ChoiceField(choices=["", "fa", "en"], required=False, allow_blank=True)
    theme = serializers.ChoiceField(choices=["", "light", "dark"], required=False, allow_blank=True)

    def validate_last_origin(self, value: str) -> str:
        return clean_place(value, "last_origin")

    def validate_last_destination(self, value: str) -> str:
        return clean_place(value, "last_destination")


class MeCountsSerializer(serializers.Serializer):
    active_requests = serializers.IntegerField()
    open_matches = serializers.IntegerField()
    pending_intros = serializers.IntegerField()
    alerts = serializers.IntegerField()


class MeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    first_name = serializers.CharField(allow_blank=True)
    counts = MeCountsSerializer(allow_null=True)
    prefs = PrefsSerializer()


class MetaSerializer(serializers.Serializer):
    api_version = serializers.IntegerField()
    min_api_version = serializers.IntegerField()
    courier_v2 = serializers.BooleanField()
    maintenance = serializers.BooleanField()


class ExchangeUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    first_name = serializers.CharField(allow_blank=True)


class ExchangeSerializer(serializers.Serializer):
    access_token = serializers.CharField()
    expires_in = serializers.IntegerField()
    user = ExchangeUserSerializer()
    courier_v2 = serializers.BooleanField()


class CitySerializer(serializers.Serializer):
    slug = serializers.CharField()
    name_fa = serializers.CharField()
    name_en = serializers.CharField()


class CountrySerializer(serializers.Serializer):
    code = serializers.CharField()
    name_fa = serializers.CharField()
    name_en = serializers.CharField()
    cities = CitySerializer(many=True)


class FrequentRouteSerializer(serializers.Serializer):
    origin = PlaceSerializer()
    destination = PlaceSerializer()


class CategoryOptionSerializer(serializers.Serializer):
    code = serializers.CharField()
    name_fa = serializers.CharField()
    name_en = serializers.CharField()


class LocationsSerializer(serializers.Serializer):
    countries = CountrySerializer(many=True)
    frequent = FrequentRouteSerializer(many=True)
    categories = CategoryOptionSerializer(many=True)


class ChannelSerializer(serializers.Serializer):
    country = serializers.CharField()
    username = serializers.CharField()
    title = serializers.CharField(allow_blank=True)


class GuideSerializer(serializers.Serializer):
    prohibited = serializers.ListField(child=serializers.CharField())
    prohibited_footer = serializers.CharField()
    main_channel = serializers.CharField(allow_null=True)
    channels = ChannelSerializer(many=True)
    bot_username = serializers.CharField(allow_null=True)


class DeepLinkSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["screen", "housing", "request", "own_request", "match", "ticket", "intro", "travelers"])
    id = serializers.IntegerField(allow_null=True)
    ids = serializers.ListField(child=serializers.IntegerField())
    screen = serializers.CharField(allow_null=True)
    read_only = serializers.BooleanField()


class TicketMessageSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    sender_type = serializers.CharField()
    message = serializers.CharField()
    created_at = serializers.DateTimeField()


class TicketSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    subject = serializers.ChoiceField(choices=TicketSubject.choices)
    status = serializers.CharField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    closed_at = serializers.DateTimeField(allow_null=True)
    closed_by = serializers.CharField(allow_null=True)
    last_message = serializers.CharField(allow_blank=True)


class TicketThreadSerializer(TicketSerializer):
    messages = TicketMessageSerializer(many=True)


class TicketsSerializer(serializers.Serializer):
    items = TicketSerializer(many=True)


# Input


class ExchangeRequestSerializer(serializers.Serializer):
    init_data = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False, max_length=8192)


class RouteQuerySerializer(serializers.Serializer):
    side = serializers.ChoiceField(choices=RequestType.choices, default=RequestType.DEMAND, help_text="The searcher's side. DEMAND has a package and sees travelers.")
    origin = serializers.CharField(required=False, allow_blank=True, default="", help_text="COUNTRY:city-slug")
    destination = serializers.CharField(required=False, allow_blank=True, default="", help_text="COUNTRY:city-slug")
    month = serializers.RegexField(r"^\d{4}-\d{2}$", required=False, allow_blank=True, default="")
    size = serializers.ChoiceField(choices=list(texts.SIZES), required=False, allow_blank=True, default="")

    def validate_origin(self, value: str) -> str:
        return clean_place(value, "origin")

    def validate_destination(self, value: str) -> str:
        return clean_place(value, "destination")


class ListQuerySerializer(RouteQuerySerializer):
    offset = serializers.IntegerField(required=False, default=0, min_value=0)
    limit = serializers.IntegerField(required=False, default=20, min_value=1, max_value=40)
    ids = serializers.CharField(required=False, allow_blank=True, default="", help_text="Comma-separated ids: only these active requests, from a shared travelers link.")


class StateQuerySerializer(serializers.Serializer):
    state = serializers.ChoiceField(choices=["active", "archive"], default="active")


class StopSerializer(serializers.Serializer):
    country = serializers.CharField(max_length=2, min_length=2)
    city = serializers.CharField(max_length=64)


class WhenSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["exact", "month", "open"])
    date = serializers.CharField(required=False, allow_blank=True, default="", max_length=32, help_text="For `exact`: 2026-12-12, 12/12 or «12 دسامبر».")
    month = serializers.CharField(required=False, allow_blank=True, default="", max_length=7, help_text="For `month`: YYYY-MM.")


class RequestFormSerializer(serializers.Serializer):
    origin = StopSerializer()
    destinations = StopSerializer(many=True, allow_empty=False)
    when = WhenSerializer()
    size = serializers.ChoiceField(choices=list(texts.SIZES), default="1")
    category_codes = serializers.ListField(child=serializers.CharField(max_length=32), required=False)
    refused_other = serializers.CharField(required=False, allow_blank=True, max_length=255, default="")
    description = serializers.CharField(required=False, allow_blank=True, max_length=500, default="")


class RequestCreateSerializer(RequestFormSerializer):
    type = serializers.ChoiceField(choices=RequestType.choices)
    allow_duplicate = serializers.BooleanField(required=False, default=False, help_text="The person saw `duplicate_request` and chose to post anyway.")


class CloseRequestSerializer(serializers.Serializer):
    package_sent = serializers.BooleanField(required=False, default=False, help_text="For a trip: the package went. A package request always cancels.")


class CityCreateSerializer(serializers.Serializer):
    country = serializers.CharField(max_length=2, min_length=2)
    name = serializers.CharField(max_length=64)


class IntroResponseSerializer(serializers.Serializer):
    accept = serializers.BooleanField()


class RateSerializer(serializers.Serializer):
    score = serializers.IntegerField()
    comment = serializers.CharField(required=False, allow_blank=True, default="")


class AlertCreateSerializer(serializers.Serializer):
    side = serializers.ChoiceField(choices=RequestType.choices)
    origin = serializers.CharField(required=False, allow_blank=True, default="", help_text="COUNTRY:city-slug")
    destination = serializers.CharField(required=False, allow_blank=True, default="", help_text="COUNTRY:city-slug")
    month = serializers.RegexField(r"^\d{4}-\d{2}$", required=False, allow_blank=True, default="")
    size = serializers.ChoiceField(choices=list(texts.SIZES), required=False, allow_blank=True, default="")

    def validate_origin(self, value: str) -> str:
        return clean_place(value, "origin")

    def validate_destination(self, value: str) -> str:
        return clean_place(value, "destination")


class AlertUpdateSerializer(serializers.Serializer):
    active = serializers.BooleanField(required=False)
    origin = serializers.CharField(required=False, allow_blank=True, help_text="COUNTRY:city-slug")
    destination = serializers.CharField(required=False, allow_blank=True, help_text="COUNTRY:city-slug")
    month = serializers.RegexField(r"^\d{4}-\d{2}$", required=False, allow_blank=True)
    size = serializers.ChoiceField(choices=list(texts.SIZES), required=False, allow_blank=True)

    def validate_origin(self, value: str) -> str:
        return clean_place(value, "origin")

    def validate_destination(self, value: str) -> str:
        return clean_place(value, "destination")


class DeepLinkRequestSerializer(serializers.Serializer):
    start = serializers.CharField(required=False, allow_blank=True, default="", max_length=128)


class TicketCreateSerializer(serializers.Serializer):
    subject = serializers.CharField(required=False, allow_blank=True, default="")
    message = serializers.CharField(required=False, allow_blank=True, default="")


class TicketReplySerializer(serializers.Serializer):
    message = serializers.CharField(required=False, allow_blank=True, default="")
