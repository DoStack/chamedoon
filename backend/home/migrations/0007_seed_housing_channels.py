from django.db import migrations

# The housing channels, created 2026-10-15. Public usernames, so they are safe in the repository.
CHANNELS = [
    ("DE", "Chamedoon_Germany"),
    ("CA", "Chamedoon_CA"),
]


def seed(apps, schema_editor):
    HomeChannel = apps.get_model("home", "HomeChannel")
    for country, username in CHANNELS:
        HomeChannel.objects.get_or_create(country=country, defaults={"username": username, "enabled": True})


def unseed(apps, schema_editor):
    apps.get_model("home", "HomeChannel").objects.filter(country__in=[row[0] for row in CHANNELS]).delete()


class Migration(migrations.Migration):
    dependencies = [("home", "0006_channels_publish")]
    operations = [migrations.RunPython(seed, unseed)]
