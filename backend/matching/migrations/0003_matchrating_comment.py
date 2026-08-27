from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matching", "0002_match_rating"),
    ]

    operations = [
        migrations.AddField(
            model_name="matchrating",
            name="comment",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
    ]
