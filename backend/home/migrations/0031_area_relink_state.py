from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('home', '0030_seed_home_areas')]

    operations = [
        migrations.AddField(
            model_name='homesettings',
            name='areas_changed_at',
            field=models.DateTimeField(blank=True, help_text='An area or channel changed. Listings checked before this get linked again by the job.', null=True),
        ),
        migrations.AddField(
            model_name='homesettings',
            name='area_backfill_after',
            field=models.BigIntegerField(default=0, help_text='Where the area backfill stopped: the last listing id it looked at.'),
        ),
    ]
