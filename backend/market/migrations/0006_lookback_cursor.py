from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("market", "0005_llm_retry"),
    ]

    operations = [
        migrations.AddField(
            model_name="marketingeststate",
            name="lookback_before_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="marketingeststate",
            name="lookback_days",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
    ]
