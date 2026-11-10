from datetime import timedelta

from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from unfold.admin import ModelAdmin
from unfold.decorators import action

from market.models import MarketIngestState, MarketPost, MarketSource, MarketSourceKind

# Same bound as System health → «Last market extract»: the extract cron runs once a day.
STALE_AFTER = timedelta(hours=26)


def _state_flag(state: MarketIngestState | None) -> str:
    if state is None or state.last_run_at is None:
        return "never read"
    if state.last_error:
        return "error"
    if timezone.now() - state.last_run_at > STALE_AFTER:
        return "stale"
    return "ok"


@admin.register(MarketPost)
class MarketPostAdmin(ModelAdmin):
    list_display = (
        "posted_at",
        "role",
        "skip_reason",
        "route_label",
        "weight_kg",
        "views",
        "item_request",
        "telegram_message_id",
        "channel_username",
    )
    list_filter = ("role", "skip_reason", "origin_country", "destination_country", "channel_username")
    search_fields = ("text", "origin_city", "destination_city", "telegram_message_id")
    date_hierarchy = "posted_at"
    ordering = ("-posted_at",)
    readonly_fields = (
        "channel_username",
        "telegram_message_id",
        "posted_at",
        "text",
        "views",
        "has_photo",
        "role",
        "origin_city",
        "origin_country",
        "destination_city",
        "destination_country",
        "weight_kg",
        "source_url",
        "author_name",
        "author_username",
        "review_json",
        "reviewed_at",
        "item_request",
        "skip_reason",
        "migrated_at",
        "ingested_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def route_label(self, obj: MarketPost) -> str:
        return obj.route_label()

    route_label.short_description = "route"


@admin.register(MarketIngestState)
class MarketIngestStateAdmin(ModelAdmin):
    readonly_fields = (
        "channel_username",
        "newest_message_id",
        "oldest_message_id",
        "backfill_complete",
        "last_run_at",
        "last_created",
        "last_updated",
        "last_error",
    )

    def has_add_permission(self, request):
        return False

    list_display = (
        "channel_username",
        "health",
        "last_run_at",
        "last_created",
        "last_updated",
        "error_short",
        "backfill_complete",
        "newest_message_id",
        "oldest_message_id",
    )

    @admin.display(description="health")
    def health(self, obj: MarketIngestState) -> str:
        return _state_flag(obj)

    @admin.display(description="last error")
    def error_short(self, obj: MarketIngestState) -> str:
        return (obj.last_error or "")[:120]


@admin.register(MarketSource)
class MarketSourceAdmin(ModelAdmin):
    """Courier crawl sources. Empty table: the server settings list is read instead (see the notice)."""

    list_display = ("username", "kind", "enabled", "retired", "health", "last_run_at", "last_created", "error_short", "note")
    list_filter = ("kind", "enabled", "retired")
    list_editable = ("enabled",)
    search_fields = ("username", "note")
    actions = ("enable_selected", "disable_selected")
    actions_list = ("import_from_settings",)

    def _states(self) -> dict[str, MarketIngestState]:
        if not hasattr(self, "_state_cache"):
            self._state_cache = {state.channel_username: state for state in MarketIngestState.objects.all()}
        return self._state_cache

    def changelist_view(self, request: HttpRequest, extra_context=None):
        self.__dict__.pop("_state_cache", None)
        if not MarketSource.objects.exists():
            from market.ingest import settings_channel_usernames
            from market.telegram_history import settings_invite_sources

            names = ", ".join([*settings_channel_usernames(), *settings_invite_sources()]) or "none"
            messages.warning(
                request,
                f"No rows yet: the crawl reads the server settings list ({names}). "
                "«Import from server settings» copies it here; from then on this table decides.",
            )
        return super().changelist_view(request, extra_context)

    def _state(self, obj: MarketSource) -> MarketIngestState | None:
        return self._states().get(obj.username)

    @admin.display(description="health")
    def health(self, obj: MarketSource) -> str:
        if obj.retired:
            return "retired"
        if not obj.enabled:
            return "off"
        if obj.kind == MarketSourceKind.GROUP and not obj.invite_hash:
            return "no invite"
        return _state_flag(self._state(obj))

    @admin.display(description="last read")
    def last_run_at(self, obj: MarketSource):
        state = self._state(obj)
        return state.last_run_at if state else None

    @admin.display(description="new last run")
    def last_created(self, obj: MarketSource):
        state = self._state(obj)
        return state.last_created if state else None

    @admin.display(description="last error")
    def error_short(self, obj: MarketSource) -> str:
        state = self._state(obj)
        return (state.last_error or "")[:120] if state else ""

    @admin.action(description="Read the selected sources")
    def enable_selected(self, request, queryset) -> None:
        self.message_user(request, f"{queryset.update(enabled=True)} source(s) on.")

    @admin.action(description="Stop reading the selected sources")
    def disable_selected(self, request, queryset) -> None:
        self.message_user(request, f"{queryset.update(enabled=False)} source(s) off.")

    @action(description="Import from server settings", url_path="import-from-settings", icon="download")
    def import_from_settings(self, request: HttpRequest):
        created = import_settings_sources()
        self.message_user(request, f"{created} source(s) added from the server settings. Existing rows were kept as they are.")
        return HttpResponseRedirect(reverse("admin:market_marketsource_changelist"))


def import_settings_sources() -> int:
    """Copy MARKET_CHANNEL_USERNAMES, MARKET_SOURCE_INVITES and the retired names into rows. Existing rows stay as they are."""
    from market.ingest import RETIRED_MARKET_SOURCES, settings_channel_usernames
    from market.telegram_history import settings_invite_sources

    created = 0
    for name in settings_channel_usernames():
        created += MarketSource.objects.get_or_create(username=name, defaults={"kind": MarketSourceKind.CHANNEL})[1]
    for key, invite in settings_invite_sources().items():
        created += MarketSource.objects.get_or_create(
            username=key, defaults={"kind": MarketSourceKind.GROUP, "invite_hash": invite}
        )[1]
    for name in RETIRED_MARKET_SOURCES:
        created += MarketSource.objects.get_or_create(
            username=name, defaults={"kind": MarketSourceKind.CHANNEL, "enabled": False, "retired": True}
        )[1]
    return created
