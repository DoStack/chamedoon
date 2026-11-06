from __future__ import annotations

from django import forms
from django.contrib import admin, messages
from django.db.models import Q
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from unfold.admin import ModelAdmin

from outreach.models import (
    ConversationState,
    OutreachAccount,
    OutreachConversation,
    OutreachFunnel,
    OutreachTurn,
    OutreachMessage,
    OutreachOptOut,
    OutreachSettings,
    OutreachState,
    OutreachStatus,
)
from outreach.queue import approve_messages, hold_messages, release_messages, skip_messages


@admin.register(OutreachMessage)
class OutreachMessageAdmin(ModelAdmin):
    list_display = (
        "id",
        "kind",
        "recipient_username",
        "city",
        "status",
        "account",
        "batch",
        "text_start",
        "error_code",
        "created_at",
        "claimed_at",
        "sent_at",
        "opened_at",
        "replied_at",
    )
    list_filter = ("status", "kind", "batch", "city", ("created_at", admin.DateFieldListFilter), ("sent_at", admin.DateFieldListFilter), "account", "error_code")
    date_hierarchy = "created_at"
    search_fields = ("recipient_username", "recipient_name", "token", "recipient_telegram_id", "text")
    ordering = ("-created_at",)
    list_select_related = ("account",)
    actions = ("approve_selected", "skip_selected", "hold_selected", "release_selected")
    fields = (
        "kind",
        "variant",
        "batch",
        "recipient",
        "recipient_username",
        "recipient_name",
        "recipient_telegram_id",
        "demand_request",
        "listing",
        "country",
        "city",
        "source_at",
        "source_post",
        "status",
        "account",
        "conversation",
        "error_code",
        "error_detail",
        "attempts",
        "preview_text",
        "matched_travelers",
        "text",
        "created_at",
        "claimed_at",
        "sent_at",
        "opened_at",
        "opened_by",
        "replied_at",
        "member_at",
        "bot_started_at",
        "active_at",
        "stopped_at",
    )
    readonly_fields = fields

    def has_add_permission(self, request) -> bool:
        return False

    @admin.display(description="Source post")
    def source_post(self, obj: OutreachMessage) -> str:
        if obj.demand_request_id:
            return obj.demand_request.source_url or "—"
        return (obj.listing.post.source_url if obj.listing_id else "") or "—"

    @admin.display(description="Text")
    def text_start(self, obj: OutreachMessage) -> str:
        first = (obj.text or "").strip().splitlines()[0] if obj.text else ""
        return first[:70]

    @admin.display(description="Text it would be sent with now")
    def preview_text(self, obj: OutreachMessage):
        from outreach.services import current_text

        text = current_text(obj, timezone.now()) if obj.status in {OutreachStatus.QUEUED, OutreachStatus.HELD} else obj.text
        return format_html('<div dir="auto" style="white-space:pre-wrap;margin:0;max-width:40rem">{}</div>', text)

    @admin.display(description="Matched travelers (live)")
    def matched_travelers(self, obj: OutreachMessage):
        from outreach.services import valid_matches

        if not obj.demand_request_id:
            return "—"
        matches = valid_matches(obj.demand_request, timezone.now())
        if not matches:
            return "No live match above the threshold: a claim would skip this message."
        rows = format_html_join(
            "",
            '<tr><td style="padding:2px 12px 2px 0">@{}</td><td style="padding:2px 12px 2px 0">{}</td>'
            '<td style="padding:2px 12px 2px 0">{} to {}</td><td style="padding:2px 12px 2px 0">{}</td><td>{}</td></tr>',
            (
                (
                    match.supply_request.user.telegram_username or "-",
                    match.supply_request.flight_date or "",
                    match.supply_request.date_from,
                    match.supply_request.date_to,
                    match.supply_request.capacity_kg if match.supply_request.capacity_kg is not None else "",
                    match.score,
                )
                for match in matches
            ),
        )
        return format_html(
            '<table><thead><tr><th style="text-align:left;padding-right:12px">Traveler</th>'
            '<th style="text-align:left;padding-right:12px">Flight</th><th style="text-align:left;padding-right:12px">Window</th>'
            '<th style="text-align:left;padding-right:12px">kg</th><th style="text-align:left">Score</th></tr></thead><tbody>{}</tbody></table>',
            rows,
        )

    @admin.action(description="Approve: queue selected held or skipped messages")
    def approve_selected(self, request, queryset) -> None:
        self.message_user(request, f"Queued {approve_messages(queryset)} message(s).", messages.SUCCESS)

    @admin.action(description="Do not send selected (skip)")
    def skip_selected(self, request, queryset) -> None:
        self.message_user(request, f"Skipped {skip_messages(queryset)} message(s).", messages.SUCCESS)

    @admin.action(description="Hold selected for review")
    def hold_selected(self, request, queryset) -> None:
        self.message_user(request, f"Held {hold_messages(queryset)} queued message(s).", messages.SUCCESS)

    @admin.action(description="Release held messages (queue them again)")
    def release_selected(self, request, queryset) -> None:
        self.message_user(request, f"Released {release_messages(queryset)} held message(s).", messages.SUCCESS)


@admin.register(OutreachOptOut)
class OutreachOptOutAdmin(ModelAdmin):
    list_display = ("telegram_username", "telegram_user_id", "source", "created_at")
    list_filter = ("source",)
    search_fields = ("telegram_username", "telegram_user_id")
    fields = ("telegram_username", "telegram_user_id", "source")

    def get_changeform_initial_data(self, request) -> dict:
        return {"source": "admin"}

    def save_model(self, request, obj, form, change) -> None:
        obj.telegram_username = (obj.telegram_username or "").strip().lstrip("@")
        super().save_model(request, obj, form, change)
        # Same as an opt-out by reply: nothing still waiting goes to this person.
        query = Q()
        if obj.telegram_username:
            query |= Q(recipient_username__iexact=obj.telegram_username)
        if obj.telegram_user_id:
            query |= Q(recipient_telegram_id=obj.telegram_user_id)
        if query:
            OutreachMessage.objects.filter(query, status__in=(OutreachStatus.QUEUED, OutreachStatus.HELD)).update(
                status=OutreachStatus.SKIPPED, error_code="opted_out", updated_at=timezone.now()
            )


@admin.register(OutreachState)
class OutreachStateAdmin(ModelAdmin):
    list_display = ("__str__", "stopped", "paused_until", "pause_reason", "last_heartbeat_at", "last_built_at")
    fields = ("stopped", "paused_until", "pause_reason", "last_heartbeat_at", "last_built_at")
    readonly_fields = ("last_heartbeat_at", "last_built_at")

    def has_add_permission(self, request) -> bool:
        return not OutreachState.objects.exists()

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


SWITCH_CHOICES = (("", "Server default (environment)"), ("true", "On"), ("false", "Off"))


class OutreachSettingsForm(forms.ModelForm):
    enabled = forms.TypedChoiceField(
        label="Outreach (build the queue)",
        choices=SWITCH_CHOICES,
        required=False,
        coerce=lambda value: {"true": True, "false": False}.get(value),
        empty_value=None,
    )
    sending_enabled = forms.TypedChoiceField(
        label="Sending",
        choices=SWITCH_CHOICES,
        required=False,
        coerce=lambda value: {"true": True, "false": False}.get(value),
        empty_value=None,
    )

    class Meta:
        model = OutreachSettings
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("enabled", "sending_enabled"):
            value = getattr(self.instance, name, None)
            self.initial[name] = "" if value is None else ("true" if value else "false")
            self.fields[name].help_text = OutreachSettings._meta.get_field(name).help_text

    def clean(self):
        data = super().clean()
        for name, high in (("send_start_hour", 23), ("send_end_hour", 24), ("min_score", 100)):
            value = data.get(name)
            if value is not None and value > high:
                self.add_error(name, f"Use 0 to {high}.")
        start, end = data.get("send_start_hour"), data.get("send_end_hour")
        if start is not None and end is not None and start >= end:
            self.add_error("send_end_hour", "The window must end after it starts.")
        return data


@admin.register(OutreachSettings)
class OutreachSettingsAdmin(ModelAdmin):
    form = OutreachSettingsForm
    fieldsets = (
        (
            "Switches",
            {
                "fields": ("enabled", "sending_enabled"),
                "description": "Take effect on the next claim or build, with no redeploy. To stop sending right away, also see Outreach state.",
            },
        ),
        ("Rollout", {"fields": ("enabled_kinds", "auto_queue"), "description": "With auto queue off, every new message waits as Held until its batch is approved (Approve action)."}),
        ("Limits", {"fields": ("daily_limit_from_accounts", "daily_limit", "account_hourly_limit", "account_daily_limit", "min_gap_minutes")}),
        ("Agent and group", {"fields": ("agent_enabled", "followups_enabled", "agent_model", "transcript_chat_id", "alert_chat_id", "operator_ids")}),
        ("Sending window (the recipient city's clock)", {"fields": ("send_start_hour", "send_end_hour")}),
        ("Who gets a message", {"fields": ("min_score", "max_post_age_days", "cooldown_days")}),
        ("In effect now", {"fields": ("effective_now",)}),
    )
    readonly_fields = ("effective_now",)

    @admin.display(description="Values in use")
    def effective_now(self, obj):
        from outreach import conf

        return format_html(
            "<table>{}</table>",
            format_html_join(
                "",
                '<tr><td style="padding:1px 16px 1px 0">{}</td><td style="padding:1px 16px 1px 0"><strong>{}</strong></td><td>{}</td></tr>',
                ((name.replace("_", " "), value, f"from {origin}") for name, (value, origin) in conf.effective().items()),
            ),
        )

    def has_add_permission(self, request) -> bool:
        return not OutreachSettings.objects.exists()

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def changelist_view(self, request, extra_context=None):
        return redirect(reverse("admin:outreach_outreachsettings_change", args=[OutreachSettings.load().pk]))


@admin.register(OutreachAccount)
class OutreachAccountAdmin(ModelAdmin):
    list_display = ("name", "enabled", "session_ok", "paused_until", "pause_reason", "hourly_limit", "daily_limit", "min_gap_minutes", "sent_today", "last_sent_at", "last_heartbeat_at", "note")
    list_editable = ("enabled", "hourly_limit", "daily_limit", "min_gap_minutes")
    fields = ("name", "enabled", "session_ok", "paused_until", "pause_reason", "hourly_limit", "daily_limit", "min_gap_minutes", "note", "last_sent_at", "next_send_at", "last_heartbeat_at")
    readonly_fields = ("last_sent_at", "next_send_at", "last_heartbeat_at")
    actions = ("enable_selected", "disable_selected")

    @admin.display(description="Attempted in 24 hours")
    def sent_today(self, obj: OutreachAccount) -> int:
        from outreach.accounts import attempted_today_for

        return attempted_today_for(obj, timezone.now())

    @admin.action(description="Switch on selected")
    def enable_selected(self, request, queryset) -> None:
        self.message_user(request, f"Switched on {queryset.update(enabled=True)} account(s).", messages.SUCCESS)

    @admin.action(description="Switch off selected")
    def disable_selected(self, request, queryset) -> None:
        self.message_user(request, f"Switched off {queryset.update(enabled=False)} account(s).", messages.SUCCESS)


class OutreachTurnInline(admin.TabularInline):
    model = OutreachTurn
    extra = 0
    can_delete = False
    fields = ("created_at", "direction", "text", "has_photo", "status", "action", "action_detail")
    readonly_fields = fields
    ordering = ("created_at", "id")

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(OutreachConversation)
class OutreachConversationAdmin(ModelAdmin):
    list_display = ("id", "username", "telegram_user_id", "account", "state", "last_inbound_at", "last_outbound_at")
    list_filter = ("state", "account")
    search_fields = ("username", "telegram_user_id")
    fields = ("ad_completion", "username", "telegram_user_id", "account", "first_message", "state", "awaiting", "hooks_done", "last_inbound_at", "last_outbound_at", "model_down_since")
    readonly_fields = ("ad_completion", "username", "telegram_user_id", "account", "first_message", "awaiting", "hooks_done", "last_inbound_at", "last_outbound_at", "model_down_since")
    inlines = (OutreachTurnInline,)
    actions = ("take_selected", "give_back_selected")

    def has_add_permission(self, request) -> bool:
        return False

    @admin.display(description="Ad completion")
    def ad_completion(self, obj: OutreachConversation):
        if not obj.pk:
            return "-"
        return format_html('<a href="{}">Their ads, what is missing, photos they sent</a>', reverse("admin:outreach_outreachconversation_complete", args=[obj.pk]))

    def get_urls(self):
        from django.urls import path

        own = [path("<int:pk>/complete/", self.admin_site.admin_view(self.completion_view), name="outreach_outreachconversation_complete")]
        return own + super().get_urls()

    def completion_view(self, request, pk: int):
        """H4 by hand: save a field, add the photos they sent, or get the next line. Only their own live ads."""
        from django.http import Http404
        from django.template.response import TemplateResponse

        from home.storage import get_storage
        from outreach import completion
        from outreach.photos import held_photos

        conversation = OutreachConversation.objects.select_related("account", "first_message").filter(pk=pk).first()
        if conversation is None:
            raise Http404
        if not self.has_change_permission(request, conversation):
            raise Http404
        by = f"operator:{request.user.get_username()}"
        result = None
        line = ""
        if request.method == "POST":
            op = request.POST.get("op", "")
            listing_id = request.POST.get("listing")
            if op == "fill":
                result = completion.apply_values(conversation, listing_id, {request.POST.get("field", ""): request.POST.get("value", "")}, by=by)
            elif op == "photos":
                turns = [int(value) for value in request.POST.getlist("turn") if value.isdigit()]
                result = completion.attach_photos(conversation, listing_id, turns, by=by) if turns else completion.Applied(False, "no_photo_ticked")
            elif op == "done":
                result = completion.finish(conversation, listing_id, by=by)
            elif op == "edit":
                listing = completion.own_ad(conversation.telegram_user_id, listing_id)
                first = conversation.first_message
                result = completion.Applied(listing is not None, "" if listing else "not_their_ad", listing, [], completion.edit_text(listing, first.token if first else "") if listing else "")
            elif op == "start":
                line = completion.start_line(conversation)
            if result is not None:
                line = result.next
            conversation.save(update_fields=["awaiting", "hooks_done", "updated_at"])
        ads = completion.live_ads(conversation.telegram_user_id)
        first = conversation.first_message
        token = first.token if first else ""
        rows = [
            {
                "listing": listing,
                "label": completion.label(listing, ads),
                "gaps": completion.gaps(listing),
                "complete": not completion.core_gaps(listing),
                "link": completion.view_link(listing, token),
                "admin_url": reverse("admin:home_homelisting_change", args=[listing.pk]),
            }
            for listing in ads
        ]
        storage = get_storage()
        photos = []
        for turn in held_photos(conversation):
            detail = turn.action_detail or {}
            state = "dropped: not housing" if detail.get("photo_dropped") else f"held: {detail.get('photo_held', 'operator')}"
            photos.append(
                {
                    "turn": turn,
                    "state": state,
                    "full": storage.url(turn.photo_key) if storage else "",
                    "thumb": storage.url(detail.get("photo_thumb") or turn.photo_key) if storage else "",
                }
            )
        if request.method == "GET" and ads:
            line = completion.which_text(ads) if len(ads) > 1 else completion.next_line(conversation, ads[0], mark=False)
        context = {
            **self.admin_site.each_context(request),
            "title": f"Ad completion #c{conversation.pk}",
            "opts": self.model._meta,
            "conversation": conversation,
            "ads": rows,
            "photos": photos,
            "fields": completion.FIELDS,
            "result": result,
            "line": line,
            "back": reverse("admin:outreach_outreachconversation_change", args=[conversation.pk]),
        }
        return TemplateResponse(request, "admin/outreach_completion.html", context)

    @admin.action(description="Take over: the agent stops answering")
    def take_selected(self, request, queryset) -> None:
        self.message_user(request, f"Took {queryset.update(state=ConversationState.HUMAN)} chat(s).", messages.SUCCESS)

    @admin.action(description="Give back to the agent")
    def give_back_selected(self, request, queryset) -> None:
        changed = queryset.exclude(state=ConversationState.STOPPED).update(state=ConversationState.OPEN)
        self.message_user(request, f"Gave back {changed} chat(s).", messages.SUCCESS)


@admin.register(OutreachFunnel)
class OutreachFunnelAdmin(ModelAdmin):
    """Sent, replied, opened, member, bot start, active, back after 7 and 30 days, stopped. Read only."""

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def changelist_view(self, request, extra_context=None):
        from django.template.response import TemplateResponse

        from outreach import funnel

        by = tuple(part for part in (request.GET.get("by") or "kind,account,variant,country").split(",") if part in {"kind", "account", "variant", "country", "city", "batch"}) or ("kind",)
        data = funnel.rows(by=by)
        head = (*by, *funnel.COLUMNS, "replied_rate", "member_rate", "stopped_rate", "verdict")
        table = format_html(
            '<p>Group by: <a href="?by=kind">model</a> · <a href="?by=account">account</a> · <a href="?by=kind,variant">wording</a> · '
            '<a href="?by=country">country</a> · <a href="?by=kind,account,variant,country">all</a>. Rates are percent of sent. Under 30 sent a row decides nothing.</p>'
            '<table class="w-full"><thead><tr>{}</tr></thead><tbody>{}</tbody></table>',
            format_html_join("", '<th style="text-align:left;padding:4px 10px">{}</th>', ((name.replace("_", " "),) for name in head)),
            format_html_join("", "<tr>{}</tr>", ((format_html_join("", '<td style="padding:4px 10px">{}</td>', ((row.get(name, ""),) for name in head)),) for row in data)),
        )
        context = {**self.admin_site.each_context(request), "title": "Outreach funnel", "content": table, "opts": self.model._meta}
        return TemplateResponse(request, "admin/outreach_funnel.html", context)
