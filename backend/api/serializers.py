from rest_framework import serializers

from item_requests.models import Category, City, Country, ItemRequest
from matching.models import Match, MatchStatus, VISIBLE_MATCH_STATUSES
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
            "date_from",
            "date_to",
            "weight_kg",
            "capacity_kg",
            "item_category_codes",
            "excluded_category_codes",
            "excluded_other_text",
            "description",
            "status",
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
            "date_from",
            "date_to",
            "weight_kg",
            "capacity_kg",
            "item_category_codes",
            "status",
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
    score_label = serializers.CharField(read_only=True)
    demand_request = RequestSummarySerializer(read_only=True)
    supply_request = RequestSummarySerializer(read_only=True)
    my_role = serializers.SerializerMethodField()
    counterpart = serializers.SerializerMethodField()

    class Meta:
        model = Match
        fields = (
            "id",
            "score",
            "score_label",
            "status",
            "demand_request",
            "supply_request",
            "my_role",
            "counterpart",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_my_role(self, match: Match) -> str | None:
        user = self.context["request"].user
        return match.role_for(user)

    def get_counterpart(self, match: Match) -> dict | None:
        if match.status != MatchStatus.CONNECTED:
            return None
        user = self.context["request"].user
        other_request = match.counterpart_request(user)
        if other_request is None:
            return None
        other = other_request.user
        username = other.telegram_username
        return {
            "first_name": other.first_name,
            "telegram_username": username,
            "telegram_user_id": other.telegram_user_id,
            "telegram_url": (
                f"https://t.me/{username}" if username else f"tg://user?id={other.telegram_user_id}"
            ),
        }

