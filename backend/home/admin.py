from django import forms
from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import reverse
from unfold.admin import ModelAdmin

from home.guard import is_courier_channel
from home.models import (
    HomeChannel,
    HomeListing,
    HomeMedia,
    HomePost,
    HomeSettings,
    HomeSource,
    ListingStatus,
    MediaStatus,
)


SECRET_FIELDS = ("r2_secret_access_key", "bot_token")


class HomeSettingsForm(forms.ModelForm):
    r2_secret_access_key = forms.CharField(
        label="R2 secret access key",
        required=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text="Create the R2 token with Object Read & Write on this bucket only.",
    )
    bot_token = forms.CharField(
        label="Home bot token",
        required=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text="Token of @chamedoon_home_bot from BotFather. Not the token of the courier bot.",
    )

    class Meta:
        model = HomeSettings
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in SECRET_FIELDS:
            if self.instance and getattr(self.instance, name):
                self.fields[name].help_text = (
                    "A secret is saved and is never shown. Leave blank to keep it, or type a new one to replace it."
                )

    def _keep(self, name: str) -> str:
        return self.cleaned_data.get(name) or getattr(self.instance, name)

    def clean_r2_secret_access_key(self) -> str:
        return self._keep("r2_secret_access_key")

    def clean_bot_token(self) -> str:
        value = self._keep("bot_token").strip()
        if value and ":" not in value:
            raise forms.ValidationError("That does not look like a bot token (it has the form 123456:ABC...).")
        return value


@admin.register(HomeSettings)
class HomeSettingsAdmin(ModelAdmin):
    form = HomeSettingsForm
    fieldsets = (
        (
            "Switches",
            {
                "fields": ("enabled", "ingest_enabled", "review_enabled", "media_enabled", "vision_enabled"),
                "description": "Take effect on the next run, with no redeploy. The master switch also stops the cron.",
            },
        ),
        ("Batch sizes", {"fields": ("lookback_days", "review_batch", "media_batch")}),
        ("AI review", {"fields": ("review_model", "review_workers")}),
        (
            "Photo storage (Cloudflare R2)",
            {
                "fields": (
                    "media_backend",
                    "r2_account_id",
                    "r2_bucket",
                    "r2_public_base",
                    "r2_access_key_id",
                    "r2_secret_access_key",
                ),
                "description": "Blank values fall back to the server environment. Photos are stored only when the backend is Cloudflare R2 and every value is filled.",
            },
        ),
        ("Photo reading (vision)", {"fields": ("vision_model",)}),
        (
            "Channel posting",
            {
                "fields": (
                    "bot_token",
                    "publish_enabled",
                    "publish_batch",
                    "publish_window_days",
                    "publish_daily_cap",
                    "publish_phone",
                ),
                "description": "Posts go to the housing channels only, through the housing bot. Keep posting off until the previews on the run page look right.",
            },
        ),
    )

    def has_add_permission(self, request) -> bool:
        return not HomeSettings.objects.exists()

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def changelist_view(self, request, extra_context=None):
        return redirect(reverse("admin:home_homesettings_change", args=[HomeSettings.load().pk]))


@admin.register(HomeSource)
class HomeSourceAdmin(ModelAdmin):
    list_display = (
        "username",
        "country",
        "default_city",
        "default_currency",
        "kind",
        "enabled",
        "is_agency",
        "fetch_photos",
        "last_run_at",
        "last_error",
    )
    list_filter = ("country", "enabled", "is_agency", "kind")
    list_editable = ("enabled",)
    search_fields = ("username", "title")
    actions = ("enable_selected", "disable_selected", "reread_from_start")
    fieldsets = (
        (None, {"fields": ("username", "title", "kind", "enabled", "is_agency", "fetch_photos")}),
        (
            "Defaults for posts that do not say",
            {"fields": ("country", "default_city", "default_region", "default_currency")},
        ),
        (
            "Private group",
            {
                "fields": ("invite_link",),
                "description": "For a private group, paste its invite link here. The member account must already be in the group.",
            },
        ),
        (
            "Reading state",
            {"fields": ("newest_message_id", "oldest_message_id", "backfill_complete", "last_run_at", "last_error")},
        ),
    )
    readonly_fields = ("newest_message_id", "oldest_message_id", "backfill_complete", "last_run_at", "last_error")

    @admin.action(description="Switch on selected")
    def enable_selected(self, request, queryset) -> None:
        self.message_user(request, f"Switched on {queryset.update(enabled=True)} source(s).", messages.SUCCESS)

    @admin.action(description="Switch off selected")
    def disable_selected(self, request, queryset) -> None:
        self.message_user(request, f"Switched off {queryset.update(enabled=False)} source(s).", messages.SUCCESS)

    @admin.action(description="Read again from the start of the lookback window")
    def reread_from_start(self, request, queryset) -> None:
        count = queryset.update(newest_message_id=None, oldest_message_id=None, backfill_complete=False)
        self.message_user(request, f"{count} source(s) will be read again; posts already stored are kept.", messages.SUCCESS)


@admin.register(HomePost)
class HomePostAdmin(ModelAdmin):
    list_display = ("posted_at", "source", "status", "reject_reason", "photo_count", "model", "telegram_message_id")
    list_filter = ("status", "reject_reason", "source")
    search_fields = ("text", "author_username")
    date_hierarchy = "posted_at"
    actions = ("review_again",)
    readonly_fields = tuple(f.name for f in HomePost._meta.fields)

    def has_add_permission(self, request) -> bool:
        return False

    @admin.action(description="Send back to the AI review")
    def review_again(self, request, queryset) -> None:
        from home.models import PostStatus

        count = queryset.exclude(status=PostStatus.ACCEPTED).update(
            status=PostStatus.NEW, reject_reason="", error="", reviewed_at=None
        )
        self.message_user(request, f"{count} post(s) will be reviewed again on the next run.", messages.SUCCESS)


@admin.register(HomeListing)
class HomeListingAdmin(ModelAdmin):
    list_display = (
        "last_seen",
        "kind",
        "property_type",
        "city",
        "area",
        "price_amount",
        "price_currency",
        "price_basis",
        "lister_type",
        "status",
        "needs_review",
    )
    list_filter = ("country", "region", "kind", "property_type", "status", "needs_review", "lister_type", "channel")
    search_fields = ("city", "area", "summary_fa", "contact_telegram", "contact_phone")
    ordering = ("-last_seen",)
    actions = ("approve_selected", "mark_expired", "skip_posting")
    readonly_fields = (
        "post",
        "canonical",
        "dedup_key",
        "first_seen",
        "last_seen",
        "created_at",
        "updated_at",
        "vision_at",
        "channel",
        "channel_message_id",
        "channel_message_ids",
        "published_at",
        "publish_attempts",
        "publish_error",
    )

    @admin.action(description="Approve (clear the review flag)")
    def approve_selected(self, request, queryset) -> None:
        self.message_user(request, f"Approved {queryset.update(needs_review=False, review_note='')} listing(s).", messages.SUCCESS)

    @admin.action(description="Do not post to the channel")
    def skip_posting(self, request, queryset) -> None:
        from django.utils import timezone

        count = queryset.filter(published_at__isnull=True).update(
            published_at=timezone.now(), publish_error="skipped by admin"
        )
        self.message_user(request, f"{count} listing(s) will not be posted.", messages.SUCCESS)

    @admin.action(description="Mark as expired")
    def mark_expired(self, request, queryset) -> None:
        self.message_user(request, f"Expired {queryset.update(status=ListingStatus.EXPIRED)} listing(s).", messages.SUCCESS)


@admin.register(HomeMedia)
class HomeMediaAdmin(ModelAdmin):
    list_display = ("created_at", "source", "post", "position", "status", "width", "height", "file_size", "error")
    list_filter = ("status", "source")
    search_fields = ("error", "storage_key")
    actions = ("retry_failed",)
    readonly_fields = tuple(f.name for f in HomeMedia._meta.fields)

    def has_add_permission(self, request) -> bool:
        return False

    @admin.action(description="Try failed photos again")
    def retry_failed(self, request, queryset) -> None:
        count = queryset.filter(status=MediaStatus.FAILED).update(status=MediaStatus.PENDING, error="")
        self.message_user(request, f"{count} photo(s) will be fetched again.", messages.SUCCESS)


class HomeChannelForm(forms.ModelForm):
    class Meta:
        model = HomeChannel
        fields = ("country", "username", "enabled")

    def clean_username(self) -> str:
        username = self.cleaned_data["username"].strip().lstrip("@")
        if is_courier_channel(username):
            raise forms.ValidationError("That is the courier channel. Housing never posts there.")
        return username


@admin.register(HomeChannel)
class HomeChannelAdmin(ModelAdmin):
    form = HomeChannelForm
    list_display = ("country", "username", "enabled", "chat_id", "title", "verified_at", "last_error")
    actions = ("check_access",)

    @admin.action(description="Check that the housing bot can post here")
    def check_access(self, request, queryset) -> None:
        from home.publish import verify_channel

        for channel in queryset:
            problem = verify_channel(channel)
            if problem:
                self.message_user(request, f"@{channel.username}: {problem}", messages.ERROR)
            else:
                self.message_user(request, f"@{channel.username}: the bot is an admin and can post.", messages.SUCCESS)
