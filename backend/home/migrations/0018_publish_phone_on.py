from django.db import migrations, models


def show_phones(apps, schema_editor):
    """Decision 2026-10-25: phone numbers are not hidden in channel posts."""
    apps.get_model("home", "HomeSettings").objects.update(publish_phone=True)


class Migration(migrations.Migration):
    dependencies = [
        ("home", "0017_alert_answers"),
    ]

    operations = [
        migrations.AlterField(
            model_name="homesettings",
            name="publish_phone",
            field=models.BooleanField(
                default=True,
                help_text="Show the advertiser's phone number in the channel post (on by default since 2026-10-25). Off: only the Telegram username and the link to the original message.",
            ),
        ),
        migrations.RunPython(show_phones, migrations.RunPython.noop),
    ]
