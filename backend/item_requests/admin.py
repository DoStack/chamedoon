from django.contrib import admin, messages
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
    list_display = ("name_en", "slug", "country", "is_active")
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
        "weight_kg",
        "capacity_kg",
    )
    list_filter = (
        "type",
        "status",
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
    )
    readonly_fields = ("created_at", "updated_at", "expires_at")
    raw_id_fields = ("user",)
    filter_horizontal = ("item_categories", "excluded_categories")
    inlines = (DemandMatchInline, SupplyMatchInline)
    actions = ("cancel_requests",)

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
