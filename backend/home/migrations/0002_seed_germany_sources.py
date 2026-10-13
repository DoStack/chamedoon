from django.db import migrations

# username, title, default city, enabled, kind. Germany pilot: the first five were read and
# measured on 2026-10-13 (30 days). The Hamburg and Cologne channels were found by search
# and stay off until someone has read a sample of their posts.
GERMANY = [
    ("berlin_khane", "برلین خانه", "Berlin", True),
    ("BerlinerMaskan", "برلینا مسکن", "Berlin", True),
    ("frankfurt_khane", "فرانکفورت خانه", "Frankfurt", True),
    ("munich_online", "مونیخ آنلاین", "Munich", True),
    ("helfenads", "هلفن، نیازمندی‌های آلمان", "", True),
    ("Khanehamburg", "خانه هامبورگ", "Hamburg", False),
    ("hamburg_khane", "هامبورگ خانه", "Hamburg", False),
    ("koln_khane", "کلن خانه", "Cologne", False),
]


def seed(apps, schema_editor):
    HomeSource = apps.get_model("home", "HomeSource")
    for username, title, city, enabled in GERMANY:
        HomeSource.objects.get_or_create(
            username=username,
            defaults={
                "title": title,
                "kind": "channel",
                "country": "DE",
                "default_city": city,
                "default_currency": "EUR",
                "enabled": enabled,
            },
        )


def unseed(apps, schema_editor):
    apps.get_model("home", "HomeSource").objects.filter(username__in=[row[0] for row in GERMANY]).delete()


class Migration(migrations.Migration):
    dependencies = [("home", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
