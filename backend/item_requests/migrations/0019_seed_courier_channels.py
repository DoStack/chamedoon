from datetime import datetime, timezone

from django.db import migrations

# The per-country courier channels, created 2026-10-23. Channel chat ids are not secret.
# Usernames stay blank until the public usernames are set. ACTIVE_FROM is fixed (not the
# migration time) so every database gets the same value: only requests created after it go out.
ACTIVE_FROM = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
CHANNELS = [
    ("CA", -1004470852135, "چمدون - باربری · کانادا"),
    ("DE", -1004412131660, "چمدون - باربری · آلمان"),
]


def seed(apps, _schema_editor) -> None:
    CourierChannel = apps.get_model("item_requests", "CourierChannel")
    for country, chat_id, title in CHANNELS:
        CourierChannel.objects.update_or_create(
            country=country,
            defaults={"chat_id": chat_id, "title": title, "username": "", "enabled": True, "active_from": ACTIVE_FROM},
        )


def unseed(apps, _schema_editor) -> None:
    CourierChannel = apps.get_model("item_requests", "CourierChannel")
    CourierChannel.objects.filter(country__in=[row[0] for row in CHANNELS]).delete()


class Migration(migrations.Migration):
    dependencies = [("item_requests", "0018_courierchannel_courierpublication")]

    operations = [migrations.RunPython(seed, unseed)]
