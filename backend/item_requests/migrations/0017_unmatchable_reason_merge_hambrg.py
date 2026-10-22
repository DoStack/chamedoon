from datetime import timedelta

from django.db import migrations, models
from django.db.models import F, Q
from django.utils import timezone

CITY_MERGES = (("DE", "hambrg", "hamburg"),)
MAX_FLIGHT_DAYS_AHEAD = 120


def _merge_stops(stops, country: str, old: str, new: str):
    merged = []
    for item in stops or []:
        if isinstance(item, dict) and str(item.get("country") or "").upper() == country:
            if (item.get("city") or item.get("slug")) == old:
                item = {**item, "city": new}
                item.pop("slug", None)
        if item not in merged:
            merged.append(item)
    return merged


def merge_city_slugs(apps, _schema_editor) -> None:
    City = apps.get_model("item_requests", "City")
    Country = apps.get_model("item_requests", "Country")
    ItemRequest = apps.get_model("item_requests", "ItemRequest")
    for country_code, old, new in CITY_MERGES:
        country = Country.objects.filter(code=country_code).first()
        if country is None:
            continue
        old_city = City.objects.filter(country=country, slug=old).first()
        if old_city is None:
            continue
        if City.objects.filter(country=country, slug=new).exists():
            old_city.delete()
        else:
            old_city.slug = new
            old_city.name_en = new.title()
            old_city.save(update_fields=["slug", "name_en"])
        ItemRequest.objects.filter(origin_country=country_code, origin_city=old).update(origin_city=new)
        ItemRequest.objects.filter(destination_country=country_code, destination_city=old).update(
            destination_city=new
        )
        for item in ItemRequest.objects.only("pk", "destination_cities").iterator():
            stops = item.destination_cities or []
            if not any(isinstance(stop, dict) and stop.get("city") == old for stop in stops):
                continue
            item.destination_cities = _merge_stops(stops, country_code, old, new)
            item.save(update_fields=["destination_cities"])


def flag_bad_imported_dates(apps, _schema_editor) -> None:
    ItemRequest = apps.get_model("item_requests", "ItemRequest")
    Match = apps.get_model("matching", "Match")
    today = timezone.now().date()
    active = ItemRequest.objects.filter(status="ACTIVE", type="SUPPLY", imported=True, unmatchable_reason="")
    flagged: list[int] = []
    inverted = active.filter(Q(date_from__gt=F("date_to")) | Q(flight_date__lt=F("date_from")))
    flagged += list(inverted.values_list("id", flat=True))
    inverted.update(unmatchable_reason="dates_inverted")
    past = active.filter(flight_date__lt=today)
    flagged += list(past.values_list("id", flat=True))
    past.update(unmatchable_reason="flight_in_past")
    far_ids = [
        item.pk
        for item in active.filter(flight_date__isnull=False).only("pk", "flight_date", "created_at")
        if item.flight_date > item.created_at.date() + timedelta(days=MAX_FLIGHT_DAYS_AHEAD)
    ]
    ItemRequest.objects.filter(pk__in=far_ids).update(unmatchable_reason="flight_too_far")
    flagged += far_ids
    if flagged:
        Match.objects.filter(
            Q(demand_request_id__in=flagged) | Q(supply_request_id__in=flagged),
            status="CONNECTED",
        ).update(status="EXPIRED", updated_at=timezone.now())


class Migration(migrations.Migration):
    dependencies = [
        ("item_requests", "0016_itemrequest_closed_status"),
        ("matching", "0005_connected_match_statuses"),
    ]

    operations = [
        migrations.AddField(
            model_name="itemrequest",
            name="unmatchable_reason",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.RunPython(merge_city_slugs, migrations.RunPython.noop),
        migrations.RunPython(flag_bad_imported_dates, migrations.RunPython.noop),
    ]
