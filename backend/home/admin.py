from django import forms
from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import reverse
from unfold.admin import ModelAdmin

from home.models import HomeListing, HomeMedia, HomePost, HomeSettings, HomeSource, ListingStatus, MediaStatus


class HomeSettingsForm(forms.ModelForm):
    r2_secret_access_key = forms.CharField(
        label="R2 secret access key",
        required=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text="Create the R2 token with Object Read & Write on this bucket only.",
    )

    class Meta:
        model = HomeSettings
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.r2_secret_access_key:
            self.fields["r2_secret_access_key"].help_text = (
                "A secret is saved and is never shown. Leave blank to keep it, or type a new one to replace it."
            )

    def clean_r2_secret_access_key(self) -> str:
        return self.cleaned_data.get("r2_secret_access_key") or self.instance.r2_secret_access_key


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
    list_filter = ("country", "region", "kind", "property_type", "status", "needs_review", "lister_type")
    search_fields = ("city", "area", "summary_fa", "contact_telegram", "contact_phone")
    ordering = ("-last_seen",)
    actions = ("approve_selected", "mark_expired")
    readonly_fields = ("post", "canonical", "dedup_key", "first_seen", "last_seen", "created_at", "updated_at", "vision_at")

    @admin.action(description="Approve (clear the review flag)")
    def approve_selected(self, request, queryset) -> None:
        self.message_user(request, f"Approved {queryset.update(needs_review=False, review_note='')} listing(s).", messages.SUCCESS)

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
