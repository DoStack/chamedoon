from django.db import migrations

from item_requests.seed import seed_catalog


def seed_forwards(_apps, _schema_editor) -> None:
    seed_catalog()


class Migration(migrations.Migration):
    dependencies = [
        ("item_requests", "0012_itemrequest_destination_cities"),
    ]

    operations = [
        migrations.RunPython(seed_forwards, migrations.RunPython.noop),
    ]
