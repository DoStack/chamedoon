from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("item_requests", "0007_seed_tier2_locations"),
    ]

    operations = [
        migrations.AddField(
            model_name="itemrequest",
            name="imported",
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
