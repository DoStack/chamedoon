from django.db import migrations, models


LIVE_STATUSES = (
    "SUGGESTED",
    "ACCEPTED_BY_DEMAND",
    "ACCEPTED_BY_SUPPLY",
    "PENDING_APPROVAL",
    "ACCEPTED",
    "CONNECTED",
)
DEAD_STATUSES = ("REJECTED", "CANCELLED")


def remap_match_statuses(apps, schema_editor):
    Match = apps.get_model("matching", "Match")
    Match.objects.filter(status__in=LIVE_STATUSES).update(status="CONNECTED")
    Match.objects.filter(status__in=DEAD_STATUSES).update(status="EXPIRED")


def reverse_match_statuses(apps, schema_editor):
    Match = apps.get_model("matching", "Match")
    Match.objects.filter(status="CONNECTED").update(status="ACCEPTED")


class Migration(migrations.Migration):
    dependencies = [
        ("matching", "0004_one_sided_match_flow"),
    ]

    operations = [
        migrations.RunPython(remap_match_statuses, reverse_match_statuses),
        migrations.AlterField(
            model_name="match",
            name="status",
            field=models.CharField(
                choices=[
                    ("CONNECTED", "Matched"),
                    ("COMPLETED", "Completed"),
                    ("EXPIRED", "Expired"),
                ],
                default="CONNECTED",
                max_length=32,
            ),
        ),
    ]
