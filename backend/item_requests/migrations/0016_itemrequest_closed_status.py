from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("item_requests", "0015_expire_past_requests"),
    ]

    operations = [
        migrations.AlterField(
            model_name="itemrequest",
            name="status",
            field=models.CharField(
                choices=[
                    ("ACTIVE", "Active"),
                    ("CLOSED", "Closed"),
                    ("CANCELLED", "Cancelled"),
                    ("EXPIRED", "Expired"),
                    ("COMPLETED", "Completed"),
                ],
                default="ACTIVE",
                max_length=16,
            ),
        ),
    ]
