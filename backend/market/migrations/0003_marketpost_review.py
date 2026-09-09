from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("market", "0002_marketpost_item_request"),
    ]

    operations = [
        migrations.AddField(
            model_name="marketpost",
            name="author_name",
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name="marketpost",
            name="author_username",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddField(
            model_name="marketpost",
            name="review_json",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="marketpost",
            name="reviewed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
