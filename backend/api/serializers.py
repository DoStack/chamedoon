from rest_framework import serializers

from item_requests.models import Category, City, Country, ItemRequest
from matching.completion import rating_state
from matching.contact import contact_for_match
from matching.models import Match
from support.models import SupportMessage, SupportTicket
from users.models import User


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "id",
            "telegram_user_id",
            "telegram_username",
            "first_name",
            "last_name",
            "is_active",
            "created_at",
        )
        read_only_fields = fields


class TelegramAuthSerializer(serializers.Serializer):
    init_data = serializers.CharField(required=False, allow_blank=True, default="")
    dev_user = serializers.DictField(required=False)


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ("code", "name_en", "name_fa", "sort_order")


class CitySerializer(serializers.ModelSerializer):
    class Meta:
        model = City
        fields = ("slug", "name_en", "name_fa")


class CountrySerializer(serializers.ModelSerializer):
    cities = serializers.SerializerMethodField()

    class Meta:
        model = Country
        fields = ("code", "name_en", "name_fa", "cities")

    def get_cities(self, country: Country) -> list[dict]:
        cities = [city for city in country.cities.all() if city.is_active]
        return CitySerializer(cities, many=True).data


class ItemRequestSerializer(serializers.ModelSerializer):
    item_category_codes = serializers.SlugRelatedField(
        source="item_categories",
        slug_field="code",
        many=True,
        read_only=True,
    )
    excluded_category_codes = serializers.SlugRelatedField(
        source="excluded_categories",
        slug_field="code",
        many=True,
        read_only=True,
    )
    match_count = serializers.SerializerMethodField()

    class Meta:
        model = ItemRequest
        fields = (
            "id",
            "type",
            "origin_country",
            "origin_city",
            "destination_country",
            "destination_city",
            "destination_cities",
            "date_from",
            "date_to",
            "desired_date",
            "flight_date",
            "weight_kg",
            "capacity_kg",
            "item_category_codes",
            "excluded_category_codes",
            "excluded_other_text",
            "description",
            "status",
            "package_sent",
            "imported",
            "expires_at",
            "match_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_match_count(self, obj: ItemRequest) -> int:
        demand_count = getattr(obj, "_demand_match_count", None)
        supply_count = getattr(obj, "_supply_match_count", None)
        if demand_count is not None and supply_count is not None:
            return int(demand_count) + int(supply_count)
        from django.db.models import Q
        from matching.models import VISIBLE_MATCH_STATUSES, Match

        return Match.objects.filter(
            Q(demand_request=obj) | Q(supply_request=obj),
            status__in=VISIBLE_MATCH_STATUSES,
        ).count()


class RequestSummarySerializer(serializers.ModelSerializer):
    item_category_codes = serializers.SlugRelatedField(
        source="item_categories",
        slug_field="code",
        many=True,
        read_only=True,
    )

    class Meta:
        model = ItemRequest
        fields = (
            "id",
            "type",
            "origin_country",
            "origin_city",
            "destination_country",
            "destination_city",
            "destination_cities",
            "date_from",
            "date_to",
            "desired_date",
            "flight_date",
            "weight_kg",
            "capacity_kg",
            "item_category_codes",
            "status",
            "imported",
        )


class OpenRequestSerializer(RequestSummarySerializer):
    owner_first_name = serializers.CharField(source="user.first_name", read_only=True)
    excluded_category_codes = serializers.SlugRelatedField(
        source="excluded_categories",
        slug_field="code",
        many=True,
        read_only=True,
    )

    class Meta(RequestSummarySerializer.Meta):
        fields = (
            *RequestSummarySerializer.Meta.fields,
            "owner_first_name",
            "excluded_category_codes",
            "description",
        )


class MatchSerializer(serializers.ModelSerializer):
    demand_request = RequestSummarySerializer(read_only=True)
    supply_request = RequestSummarySerializer(read_only=True)
    my_role = serializers.SerializerMethodField()
    is_owner = serializers.SerializerMethodField()
    is_requester = serializers.SerializerMethodField()
    counterpart = serializers.SerializerMethodField()
    can_complete = serializers.SerializerMethodField()
    can_rate = serializers.SerializerMethodField()

    class Meta:
        model = Match
        fields = (
            "id",
            "status",
            "demand_request",
            "supply_request",
            "my_role",
            "is_owner",
            "is_requester",
            "counterpart",
            "can_complete",
            "can_rate",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_my_role(self, match: Match) -> str | None:
        user = self.context["request"].user
        return match.role_for(user)

    def get_is_owner(self, match: Match) -> bool:
        return match.is_owner(self.context["request"].user)

    def get_is_requester(self, match: Match) -> bool:
        return match.is_requester(self.context["request"].user)

    def get_counterpart(self, match: Match) -> dict | None:
        return contact_for_match(match, self.context["request"].user)

    def _state(self, match: Match) -> dict:
        cache = getattr(self, "_rating_cache", None)
        if cache is None:
            self._rating_cache = {}
            cache = self._rating_cache
        if match.pk not in cache:
            cache[match.pk] = rating_state(match, self.context["request"].user)
        return cache[match.pk]

    def get_can_complete(self, match: Match) -> bool:
        return self._state(match)["can_complete"]

    def get_can_rate(self, match: Match) -> bool:
        return self._state(match)["can_rate"]


class SupportMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupportMessage
        fields = ("id", "sender_type", "sender_id", "message", "created_at")
        read_only_fields = fields


class SupportTicketSerializer(serializers.ModelSerializer):
    messages = SupportMessageSerializer(many=True, read_only=True)
    last_activity_at = serializers.DateTimeField(source="updated_at", read_only=True)

    class Meta:
        model = SupportTicket
        fields = (
            "id",
            "subject",
            "status",
            "created_at",
            "updated_at",
            "last_activity_at",
            "closed_at",
            "closed_by",
            "messages",
        )
        read_only_fields = fields

