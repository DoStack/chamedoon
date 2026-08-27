from __future__ import annotations

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from matching.manual import create_manual_match, set_match_status, validate_manual_pair
from matching.models import Match, MatchStatus
from unfold.admin import ModelAdmin


class ScoreBandFilter(admin.SimpleListFilter):
    title = _("score")
    parameter_name = "score_band"

    def lookups(self, request, model_admin):
        return (
            ("strong", "STRONG (80–100)"),
            ("possible", "POSSIBLE (60–79)"),
            ("weak", "WEAK (<60)"),
        )

    def queryset(self, request, queryset):
        if self.value() == "strong":
            return queryset.filter(score__gte=80)
        if self.value() == "possible":
            return queryset.filter(score__gte=60, score__lt=80)
        if self.value() == "weak":
            return queryset.filter(score__lt=60)
        return queryset


class MatchAdminForm(forms.ModelForm):
    override_rules = forms.BooleanField(
        required=False,
        label="Override hard rules",
        help_text="Force this pair even if origin, dates, capacity, or exclusions would block it.",
    )

    class Meta:
        model = Match
        fields = ("demand_request", "supply_request", "status")

    def clean(self):
        cleaned = super().clean()
        if self.instance.pk:
            return cleaned
        demand = cleaned.get("demand_request")
        supply = cleaned.get("supply_request")
        if not demand or not supply:
            return cleaned
        try:
            validate_manual_pair(
                demand,
                supply,
                override_rules=cleaned.get("override_rules") or False,
            )
        except ValidationError as exc:
            raise forms.ValidationError(exc.message_dict if hasattr(exc, "message_dict") else exc.messages) from exc
        return cleaned


@admin.register(Match)
class MatchAdmin(ModelAdmin):
    form = MatchAdminForm
    list_display = (
        "id",
        "status",
        "score",
        "score_label",
        "origin",
        "destination",
        "demand_request",
        "supply_request",
        "created_at",
    )
    list_filter = ("status", ScoreBandFilter, "created_at")
    date_hierarchy = "created_at"
    search_fields = (
        "demand_request__origin_city",
        "demand_request__destination_city",
        "supply_request__origin_city",
        "supply_request__destination_city",
        "demand_request__origin_country",
        "demand_request__destination_country",
    )
    readonly_fields = ("score", "created_at", "updated_at")
    raw_id_fields = ("demand_request", "supply_request")
    actions = ("mark_connected", "mark_expired", "mark_rejected")

    @admin.display(description="Origin")
    def origin(self, match: Match) -> str:
        request = match.demand_request
        return f"{request.origin_city}, {request.origin_country}"

    @admin.display(description="Destination")
    def destination(self, match: Match) -> str:
        request = match.demand_request
        return f"{request.destination_city}, {request.destination_country}"

    def save_model(self, request, obj, form, change):
        if not change:
            match = create_manual_match(
                obj.demand_request,
                obj.supply_request,
                status=obj.status or MatchStatus.SUGGESTED,
                override_rules=form.cleaned_data.get("override_rules") or False,
            )
            obj.pk = match.pk
            obj.score = match.score
            obj.status = match.status
            obj.created_at = match.created_at
            obj.updated_at = match.updated_at
            return
        previous_status = Match.objects.filter(pk=obj.pk).values_list("status", flat=True).first()
        super().save_model(request, obj, form, change)
        if obj.status == MatchStatus.CONNECTED and previous_status != MatchStatus.CONNECTED:
            from notifications.services import notify_connected

            notify_connected(obj)

    @admin.action(description="Mark selected matches CONNECTED")
    def mark_connected(self, request, queryset):
        self._set_status(request, queryset, MatchStatus.CONNECTED)

    @admin.action(description="Mark selected matches EXPIRED")
    def mark_expired(self, request, queryset):
        self._set_status(request, queryset, MatchStatus.EXPIRED)

    @admin.action(description="Mark selected matches REJECTED")
    def mark_rejected(self, request, queryset):
        self._set_status(request, queryset, MatchStatus.REJECTED)

    def _set_status(self, request, queryset, status: str) -> None:
        updated = 0
        for match in queryset:
            set_match_status(match, status)
            updated += 1
        self.message_user(request, f"Updated {updated} match(es) to {status}.", messages.SUCCESS)
