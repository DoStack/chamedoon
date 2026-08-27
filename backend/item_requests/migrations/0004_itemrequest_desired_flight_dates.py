from django.db import migrations, models


def fill_new_dates(apps, schema_editor):
    ItemRequest = apps.get_model("item_requests", "ItemRequest")
    for item in ItemRequest.objects.all().iterator():
        if item.type == "DEMAND":
            item.desired_date = item.date_from
            item.date_to = item.date_from
            item.save(update_fields=["desired_date", "date_to"])
        else:
            item.flight_date = item.date_from
            item.save(update_fields=["flight_date"])


class Migration(migrations.Migration):
    dependencies = [
        ("item_requests", "0003_itemrequest_channel"),
    ]

    operations = [
        migrations.AddField(
            model_name="itemrequest",
            name="desired_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="itemrequest",
            name="flight_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.RunPython(fill_new_dates, migrations.RunPython.noop),
    ]
