from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="MarketIngestState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("channel_username", models.CharField(max_length=64, unique=True)),
                ("newest_message_id", models.BigIntegerField(blank=True, null=True)),
                ("oldest_message_id", models.BigIntegerField(blank=True, null=True)),
                ("backfill_complete", models.BooleanField(default=False)),
                ("last_run_at", models.DateTimeField(blank=True, null=True)),
                ("last_created", models.PositiveIntegerField(default=0)),
                ("last_updated", models.PositiveIntegerField(default=0)),
                ("last_error", models.TextField(blank=True)),
            ],
            options={
                "verbose_name": "market ingest state",
                "verbose_name_plural": "market ingest state",
            },
        ),
        migrations.CreateModel(
            name="MarketPost",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("channel_username", models.CharField(db_index=True, max_length=64)),
                ("telegram_message_id", models.BigIntegerField()),
                ("posted_at", models.DateTimeField(db_index=True)),
                ("text", models.TextField()),
                ("views", models.PositiveIntegerField(blank=True, null=True)),
                ("has_photo", models.BooleanField(default=False)),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("supply", "Traveler / can carry"),
                            ("demand", "Sender / needs carry"),
                            ("noise", "Ad or off-topic"),
                            ("unknown", "Unclassified"),
                        ],
                        db_index=True,
                        default="unknown",
                        max_length=16,
                    ),
                ),
                ("origin_city", models.CharField(blank=True, max_length=64)),
                ("origin_country", models.CharField(blank=True, db_index=True, max_length=2)),
                ("destination_city", models.CharField(blank=True, max_length=64)),
                ("destination_country", models.CharField(blank=True, db_index=True, max_length=2)),
                ("weight_kg", models.DecimalField(blank=True, decimal_places=1, max_digits=6, null=True)),
                ("source_url", models.URLField(blank=True, max_length=255)),
                ("ingested_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-posted_at", "-telegram_message_id"],
            },
        ),
        migrations.AddConstraint(
            model_name="marketpost",
            constraint=models.UniqueConstraint(
                fields=("channel_username", "telegram_message_id"),
                name="market_post_channel_message_uniq",
            ),
        ),
        migrations.AddIndex(
            model_name="marketpost",
            index=models.Index(fields=["channel_username", "-posted_at"], name="market_post_ch_posted_idx"),
        ),
    ]
