from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("home", "0041_join_gate_groups")]

    operations = [
        migrations.AddField(
            model_name="homesettings",
            name="assistant_on",
            field=models.BooleanField(
                default=True,
                help_text="The housing bot talks with people through the AI (search, alerts, posting). Off: every message gets the fallback that sends them to the Mini App.",
            ),
        ),
    ]
