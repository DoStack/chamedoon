from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("market", "0003_marketpost_review"),
    ]

    operations = [
        migrations.AddField(
            model_name="marketingeststate",
            name="extract_cursor_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
    ]
