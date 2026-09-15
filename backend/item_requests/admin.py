from django.contrib import admin, messages
from django.core.exceptions import ObjectDoesNotExist
from django.urls import reverse
from django.utils.html import format_html
from unfold.admin import ModelAdmin, TabularInline

from item_requests.models import Category, City, Country, ItemRequest, RequestStatus
from item_requests.services import cancel_item_request
from matching.models import Match


class DemandMatchInline(TabularInline):
    model = Match
    fk_name = "demand_request"
    extra = 0
    show_change_link = True
    readonly_fields = ("status", "score", "supply_request", "created_at")
    fields = ("status", "score", "supply_request", "created_at")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class SupplyMatchInline(TabularInline):
    model = Match
    fk_name = "supply_request"
    extra = 0
    show_change_link = True
    readonly_fields = ("status", "score", "demand_request", "created_at")
    fields = ("status", "score", "demand_request", "created_at")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Category)
class CategoryAdmin(ModelAdmin):
    list_display = ("code", "name_en", "name_fa", "is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("code", "name_en", "name_fa")


@admin.register(Country)
class CountryAdmin(ModelAdmin):
    list_display = ("code", "name_en", "name_fa", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "name_en", "name_fa")


@admin.register(City)
class CityAdmin(ModelAdmin):
    list_display = ("name_en", "name_fa", "slug", "country", "is_active")
    list_filter = ("country", "is_active")
    search_fields = ("name_en", "name_fa", "slug")


@admin.register(ItemRequest)
class ItemRequestAdmin(ModelAdmin):
    list_display = (
        "id",
        "type",
        "status",
        "user",
        "origin_country",
        "origin_city",
        "destination_country",
        "destination_city",
        "date_from",
        "date_to",
        "desired_date",
        "flight_date",
        "weight_kg",
        "capacity_kg",
        "package_sent",
        "imported",
        "market_post_link",
        "source_link",
        "channel_status",
    )
    list_filter = (
        "type",
        "status",
        "channel_status",
        "package_sent",
        "imported",
        "origin_country",
        "destination_country",
        "date_from",
        "item_categories",
    )
    date_hierarchy = "date_from"
    search_fields = (
        "origin_city",
        "destination_city",
        "origin_country",
        "destination_country",
        "user__first_name",
        "user__telegram_username",
        "user__telegram_user_id",
        "source_url",
        "market_post__channel_username",
        "market_post__telegram_message_id",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
        "expires_at",
        "source_url",
        "source_link",
        "market_post_link",
        "channel_message_id",
        "channel_published_at",
        "channel_status",
    )
    raw_id_fields = ("user",)
    filter_horizontal = ("item_categories", "excluded_categories")
    inlines = (DemandMatchInline, SupplyMatchInline)
    actions = (
        "cancel_requests",
        "publish_to_channel",
        "retry_channel_publication",
        "update_channel_post",
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("user", "market_post")

    def _market_post(self, obj: ItemRequest):
        try:
            return obj.market_post
        except ObjectDoesNotExist:
            return None

    @admin.display(description="Market post")
    def market_post_link(self, obj: ItemRequest) -> str:
        post = self._market_post(obj)
        if post is None:
            return "—"
        url = reverse("admin:market_marketpost_change", args=[post.pk])
        label = f"#{post.pk} @{post.channel_username}/{post.telegram_message_id}"
        return format_html('<a href="{}">{}</a>', url, label)

    @admin.display(description="Source message")
    def source_link(self, obj: ItemRequest) -> str:
        url = (obj.source_url or "").strip()
        if not url:
            return "—"
        return format_html('<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>', url, url)

    @admin.action(description="Cancel selected active requests")
    def cancel_requests(self, request, queryset):
        cancelled = 0
        skipped = 0
        for item_request in queryset:
            if item_request.status != RequestStatus.ACTIVE:
                skipped += 1
                continue
            cancel_item_request(item_request)
            cancelled += 1
        self.message_user(
            request,
            f"Cancelled {cancelled} request(s). Skipped {skipped}.",
            messages.SUCCESS,
        )

    @admin.action(description="Publish to Channel")
    def publish_to_channel(self, request, queryset):
        self._sync_channel(request, queryset)

    @admin.action(description="Retry Channel Publication")
    def retry_channel_publication(self, request, queryset):
        self._sync_channel(request, queryset)

    @admin.action(description="Update Channel Post")
    def update_channel_post(self, request, queryset):
        self._sync_channel(request, queryset)

    def _sync_channel(self, request, queryset) -> None:
        from notifications.channel import sync_request_channel

        published = 0
        failed = 0
        for item_request in queryset:
            if sync_request_channel(item_request.pk):
                published += 1
            else:
                failed += 1
        self.message_user(
            request,
            f"Channel sync finished. Updated {published}, failed or skipped {failed}.",
            messages.SUCCESS if failed == 0 else messages.WARNING,
        )
