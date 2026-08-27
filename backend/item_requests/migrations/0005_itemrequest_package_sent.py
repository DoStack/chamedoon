from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("item_requests", "0004_itemrequest_desired_flight_dates"),
    ]

    operations = [
        migrations.AddField(
            model_name="itemrequest",
            name="package_sent",
            field=models.BooleanField(blank=True, default=None, null=True),
        ),
    ]
