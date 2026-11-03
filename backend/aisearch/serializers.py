from __future__ import annotations

from rest_framework import serializers

STATUSES = ("parsed", "simple", "capped", "unknown_area", "no_city", "not_understood", "other_city")
SOURCES = ("rules", "llm", "regex")
MAX_TEXT = 200


class SearchParseRequestSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=MAX_TEXT, help_text="What the person typed.")
    country = serializers.CharField(required=False, allow_blank=True, max_length=2)
    city = serializers.CharField(required=False, allow_blank=True, max_length=64, help_text="Metro name or slug. Blank: no_city.")
    rules_only = serializers.BooleanField(required=False, default=False, help_text="For the live suggestions of F08#2: no model, no cache, no log, no cap.")


class SearchSpecSerializer(serializers.Serializer):
    """The list screen's filters, by the names of its URL keys. Size is applied by the search screen."""

    kind = serializers.CharField()
    types = serializers.ListField(child=serializers.CharField())
    min = serializers.IntegerField(allow_null=True)
    max = serializers.IntegerField(allow_null=True)
    size_min = serializers.IntegerField(allow_null=True)
    size_max = serializers.IntegerField(allow_null=True)
    area_ids = serializers.ListField(child=serializers.IntegerField())
    term = serializers.CharField(allow_blank=True)
    furnished = serializers.BooleanField()
    bills = serializers.BooleanField()
    owner = serializers.BooleanField()
    warm = serializers.BooleanField()
    reg = serializers.BooleanField()
    nowbs = serializers.BooleanField()
    gender = serializers.CharField(allow_blank=True)
    move = serializers.CharField(allow_blank=True)
    by = serializers.CharField(allow_blank=True)
    q = serializers.CharField(allow_blank=True)


class SearchAreaLabelSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    label = serializers.CharField(help_text="The chip: the area's name, or the intersection as typed.")
    name = serializers.CharField(help_text="The area's name, as the list's `areas` filter reads it.")


class SearchUnknownAreaSerializer(serializers.Serializer):
    text = serializers.CharField()
    suggestions = SearchAreaLabelSerializer(many=True)


class SearchOtherCitySerializer(serializers.Serializer):
    slug = serializers.CharField()
    name_fa = serializers.CharField()
    country = serializers.CharField()


class SearchParseSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[(value, value) for value in STATUSES], help_text="parsed F08#4, simple #8, capped #9, unknown_area #6, no_city #7, not_understood #12, other_city #13.")
    source = serializers.ChoiceField(choices=[(value, value) for value in SOURCES])
    spec = SearchSpecSerializer()
    area_labels = SearchAreaLabelSerializer(many=True)
    ignored = serializers.ListField(child=serializers.CharField(), help_text="Words not counted. Shown in F08#8.")
    unknown_area = SearchUnknownAreaSerializer(allow_null=True)
    other_city = SearchOtherCitySerializer(allow_null=True)
    cached = serializers.BooleanField()


class SearchRecentRowSerializer(serializers.Serializer):
    text = serializers.CharField()
    spec = SearchSpecSerializer()
    area_labels = SearchAreaLabelSerializer(many=True)


class SearchRecentSerializer(serializers.Serializer):
    results = SearchRecentRowSerializer(many=True)


class SearchClearedSerializer(serializers.Serializer):
    cleared_at = serializers.DateTimeField(help_text="Send back to restore this set (F08#11).")
