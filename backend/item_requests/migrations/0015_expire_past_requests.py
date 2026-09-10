from django.db import migrations
from django.db.models import Q
from django.utils import timezone


def expire_past_requests(apps, _schema_editor) -> None:
    ItemRequest = apps.get_model("item_requests", "ItemRequest")
    Match = apps.get_model("matching", "Match")
    now = timezone.now()
    today = now.date()
    expired_ids = list(
        ItemRequest.objects.filter(status="ACTIVE")
        .filter(
            Q(expires_at__lte=now)
            | Q(type="DEMAND", desired_date__lt=today)
            | Q(type="DEMAND", desired_date__isnull=True, date_to__lt=today)
            | Q(type="SUPPLY", date_to__lt=today, flight_date__lt=today)
            | Q(type="SUPPLY", date_to__lt=today, flight_date__isnull=True)
        )
        .values_list("id", flat=True)
    )
    if not expired_ids:
        return
    ItemRequest.objects.filter(id__in=expired_ids).update(status="EXPIRED", updated_at=now)
    Match.objects.filter(
        Q(demand_request_id__in=expired_ids) | Q(supply_request_id__in=expired_ids),
        status__in=(
            "SUGGESTED",
            "ACCEPTED_BY_SUPPLY",
            "ACCEPTED_BY_DEMAND",
            "CONNECTED",
        ),
    ).update(status="EXPIRED", updated_at=now)


class Migration(migrations.Migration):
    dependencies = [
        ("item_requests", "0014_seed_augsburg"),
        ("matching", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(expire_past_requests, migrations.RunPython.noop),
    ]
