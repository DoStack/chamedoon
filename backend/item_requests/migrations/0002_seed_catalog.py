from django.db import migrations

from item_requests.seed import seed_catalog


def seed_forwards(_apps, _schema_editor) -> None:
    seed_catalog()


def seed_backwards(apps, _schema_editor) -> None:
    Category = apps.get_model("item_requests", "Category")
    City = apps.get_model("item_requests", "City")
    Country = apps.get_model("item_requests", "Country")
    Category.objects.all().delete()
    City.objects.all().delete()
    Country.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("item_requests", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_forwards, seed_backwards),
    ]
