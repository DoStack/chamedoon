from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("item_requests", "0002_seed_catalog"),
    ]

    operations = [
        migrations.AddField(
            model_name="itemrequest",
            name="channel_message_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="itemrequest",
            name="channel_published_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="itemrequest",
            name="channel_status",
            field=models.CharField(
                choices=[
                    ("NOT_PUBLISHED", "Not published"),
                    ("PUBLISHED", "Published"),
                    ("UPDATED", "Updated"),
                    ("FAILED", "Failed"),
                ],
                default="NOT_PUBLISHED",
                max_length=16,
            ),
        ),
    ]
