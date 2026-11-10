from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("home", "0046_content_weekly_v1")]

    operations = [
        migrations.AddField(model_name="homesource", name="gap_top_id", field=models.BigIntegerField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name="homesource", name="gap_cursor_id", field=models.BigIntegerField(blank=True, editable=False, null=True)),
    ]
