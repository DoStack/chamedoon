from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


SYNTHETIC_TELEGRAM_USER_ID = 9_000_000_000_000


def mark_synthetic_market_users(apps, schema_editor):
    User = apps.get_model("users", "User")
    User.objects.filter(telegram_user_id__gte=SYNTHETIC_TELEGRAM_USER_ID).update(from_market=True)


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="first_started_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="from_market",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="user",
            name="last_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="start_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.CreateModel(
            name="BotStart",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("telegram_user_id", models.BigIntegerField(db_index=True)),
                ("payload", models.CharField(blank=True, default="", max_length=64)),
                ("started_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="bot_starts",
                        to="users.user",
                    ),
                ),
            ],
            options={
                "ordering": ["-started_at"],
            },
        ),
        migrations.RunPython(mark_synthetic_market_users, migrations.RunPython.noop),
    ]
