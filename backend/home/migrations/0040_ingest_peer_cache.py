from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("home", "0039_listing_owner_claim_stats"),
    ]

    operations = [
        migrations.AddField(
            model_name="homesource",
            name="tg_peer_id",
            field=models.BigIntegerField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="homesource",
            name="tg_access_hash",
            field=models.BigIntegerField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="homesource",
            name="tg_peer_kind",
            field=models.CharField(blank=True, editable=False, help_text="channel or chat.", max_length=8),
        ),
        migrations.AddField(
            model_name="homesource",
            name="tg_public_username",
            field=models.CharField(blank=True, editable=False, max_length=64),
        ),
        migrations.AddField(
            model_name="homesettings",
            name="ingest_paused_until",
            field=models.DateTimeField(
                blank=True,
                help_text="Set when Telegram asks the reader to wait (FloodWait). No source is read before this time.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="homesettings",
            name="ingest_pause_reason",
            field=models.CharField(blank=True, max_length=300),
        ),
    ]
