from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("item_requests", "0011_seed_hanover"),
    ]

    operations = [
        migrations.AddField(
            model_name="itemrequest",
            name="destination_cities",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
