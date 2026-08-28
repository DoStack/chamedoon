import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("item_requests", "0008_itemrequest_imported"),
        ("market", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="marketpost",
            name="item_request",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="market_post",
                to="item_requests.itemrequest",
            ),
        ),
        migrations.AddField(
            model_name="marketpost",
            name="migrated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="marketpost",
            name="skip_reason",
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
