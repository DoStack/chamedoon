from django.db import migrations

# username, title, kind, default city, region, enabled, agency.
# The first block was read and measured on 2026-10-13 (30 days). The rest were found by
# Telegram search the same day and stay off until someone has read a sample of their posts.
# The private Montreal group is read through its invite hash, which is NOT stored here
# (the repo is public): set HOME_SOURCE_INVITES=khane_montreal:<hash> on the server.
CANADA = [
    ("RentToronto", "اجاره خانه در تورنتو", "group", "Toronto", "ON", True, False),
    ("Toronto_Rental", "اجاره خانه در تورنتو", "group", "Toronto", "ON", True, False),
    ("Toronto_Apartment_House", "خانه و آپارتمان تورنتو", "group", "Toronto", "ON", True, False),
    ("Iranianmontrealhome", "خانه مونترال (خرید، فروش، اجاره)", "group", "Montreal", "QC", True, False),
    ("buyhouseinMT", "خرید یا اجاره خانه در مونترال", "group", "Montreal", "QC", True, False),
    ("khane_montreal", "خانه مونترال (گروه خصوصی)", "group", "Montreal", "QC", True, False),
    ("CanadaHome", "Home Canada املاک مونترال", "channel", "Montreal", "QC", True, True),
    ("EjareToronto", "اجاره کوتاه مدت تورنتو", "group", "Toronto", "ON", False, False),
    ("Toronto_Iranians", "کاریابی و اجاره تورنتو", "group", "Toronto", "ON", False, False),
    ("torontorentalmarket", "اجاره منزل و اتاق در تورنتو", "group", "Toronto", "ON", False, False),
    ("buy_renttoronto", "خرید و اجاره املاک در تورنتو", "group", "Toronto", "ON", False, False),
    ("HouseToronto", "خرید و اجاره خانه در تورنتو", "group", "Toronto", "ON", False, False),
    ("montrealhouses", "Montreal Home", "group", "Montreal", "QC", False, False),
    ("MontrealRentHome", "اجاره خانه در مونترال", "group", "Montreal", "QC", False, False),
    ("VancouverHousesAndApartments", "خانه و آپارتمان ونکوور", "group", "Vancouver", "BC", False, False),
    ("RentHomesVancouver", "خانه های اجاره ای ونکوور", "group", "Vancouver", "BC", False, False),
    ("vancouverhomesss", "اجاره و خرید خانه در ونکوور", "group", "Vancouver", "BC", False, False),
    ("CANADARENT", "کانادا اجاره املاک", "channel", "", "", False, False),
    ("EjarehCanada", "اجاره کوتاه مدت کانادا", "group", "", "", False, False),
]


def seed(apps, schema_editor):
    HomeSource = apps.get_model("home", "HomeSource")
    for username, title, kind, city, region, enabled, agency in CANADA:
        HomeSource.objects.get_or_create(
            username=username,
            defaults={
                "title": title,
                "kind": kind,
                "country": "CA",
                "default_city": city,
                "default_region": region,
                "default_currency": "CAD",
                "enabled": enabled,
                "is_agency": agency,
            },
        )


def unseed(apps, schema_editor):
    apps.get_model("home", "HomeSource").objects.filter(username__in=[row[0] for row in CANADA]).delete()


class Migration(migrations.Migration):
    dependencies = [("home", "0003_photos_region_author")]
    operations = [migrations.RunPython(seed, unseed)]
