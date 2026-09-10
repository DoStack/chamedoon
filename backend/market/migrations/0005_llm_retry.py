from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("market", "0004_extract_cursor"),
    ]

    operations = [
        migrations.AddField(
            model_name="marketpost",
            name="llm_retry_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
