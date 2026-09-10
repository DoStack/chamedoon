from django.db import migrations, models
import django.db.models.deletion


STATUS_MAP = {
    "SUGGESTED": "PENDING_APPROVAL",
    "ACCEPTED_BY_DEMAND": "PENDING_APPROVAL",
    "ACCEPTED_BY_SUPPLY": "PENDING_APPROVAL",
    "CONNECTED": "ACCEPTED",
}


def remap_match_statuses(apps, schema_editor):
    Match = apps.get_model("matching", "Match")
    for old, new in STATUS_MAP.items():
        Match.objects.filter(status=old).update(status=new)


def set_initiated_by(apps, schema_editor):
    Match = apps.get_model("matching", "Match")
    for match in Match.objects.select_related("demand_request").iterator():
        if match.initiated_by_id:
            continue
        match.initiated_by_id = match.demand_request.user_id
        match.save(update_fields=["initiated_by"])


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0001_initial"),
        ("matching", "0003_matchrating_comment"),
    ]

    operations = [
        migrations.AddField(
            model_name="match",
            name="initiated_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="initiated_matches",
                to="users.user",
            ),
        ),
        migrations.RunPython(remap_match_statuses, migrations.RunPython.noop),
        migrations.RunPython(set_initiated_by, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="match",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING_APPROVAL", "Waiting for approval"),
                    ("ACCEPTED", "Accepted"),
                    ("REJECTED", "Rejected"),
                    ("CANCELLED", "Cancelled"),
                    ("EXPIRED", "Expired"),
                    ("COMPLETED", "Completed"),
                ],
                default="PENDING_APPROVAL",
                max_length=32,
            ),
        ),
    ]
