from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("courier", "0004_intro_undelivered_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="couriersettings",
            name="channel_digest_on",
            field=models.BooleanField(
                default=False,
                help_text="Channels get the daily board (courier_digest) instead of one post per request. On: no per-request channel posts.",
            ),
        ),
        migrations.CreateModel(
            name="CourierDigestPost",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("scope", models.CharField(max_length=8)),
                ("day", models.DateField(help_text="Local date of the board in the channel's timezone.")),
                ("chat_id", models.BigIntegerField(blank=True, null=True)),
                ("message_id", models.BigIntegerField(blank=True, null=True)),
                ("status", models.CharField(default="sent", help_text="sent, skipped or failed.", max_length=16)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("error", models.CharField(blank=True, default="", max_length=300)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-day", "scope"]},
        ),
        migrations.AddConstraint(
            model_name="courierdigestpost",
            constraint=models.UniqueConstraint(fields=("scope", "day"), name="courier_digest_scope_day"),
        ),
    ]
