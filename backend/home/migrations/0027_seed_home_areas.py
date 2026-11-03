from django.db import migrations

from home.area_seed import SEED


def seed(apps, schema_editor):
    HomeArea = apps.get_model("home", "HomeArea")
    for city, country, rows in SEED:
        for rank, (canonical, fa, group, lat, lng, aliases) in enumerate(rows):
            HomeArea.objects.get_or_create(
                country=country,
                city=city,
                canonical=canonical,
                defaults={"fa": fa, "group": group, "lat": lat, "lng": lng, "aliases": aliases, "rank": rank},
            )


class Migration(migrations.Migration):
    dependencies = [("home", "0026_home_area_and_saved")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
