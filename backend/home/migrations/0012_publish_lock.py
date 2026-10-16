from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('home', '0011_review_model_workers'),
    ]

    operations = [
        migrations.AddField(
            model_name='homesettings',
            name='publish_lock_until',
            field=models.DateTimeField(blank=True, help_text='Set while posts are going out, so two runs never post the same listing twice.', null=True),
        ),
    ]
