from datetime import datetime, timezone

from django.db import migrations

# The housing city channels, created 2026-10-20. Public usernames and chat ids, so they are
# safe in the repository. Aliases are the suburbs and spellings the review writes as the city.
# Listings already in the country channel before CUTOVER were copied into the city channels
# by hand on 2026-10-21 (last copy 22:09 UTC), so the city pass skips them.
CUTOVER = datetime(2026, 10, 21, 22, 10, tzinfo=timezone.utc)
CITY_CHANNELS = [
    (
        "CA",
        "Toronto",
        "chamedoon_toronto_khane",
        -1003975467696,
        "North York, Richmond Hill, Newmarket, Aurora, Holland Landing, East Gwillimbury, Scarborough, Etobicoke, "
        "Thornhill, Markham, Vaughan, Maple, King City, Stouffville, Mississauga, Brampton, Oakville, Burlington, "
        "Milton, Pickering, Ajax, Whitby, Oshawa, Bradford, York, East York, Woodbridge, تورنتو",
    ),
    (
        "CA",
        "Montreal",
        "chamedoon_montreal_khane",
        -1004348540244,
        "Montréal, Longueuil, Laval, Brossard, Côte Saint-Luc, Cote Saint-Luc, Westmount, Verdun, LaSalle, Dorval, "
        "Pointe-Claire, Saint-Laurent, Outremont, Mont-Royal, Dollard-des-Ormeaux, Kirkland, مونترال",
    ),
    (
        "CA",
        "Vancouver",
        "chamedoon_vancouver_khane",
        -1004326051425,
        "North Vancouver, West Vancouver, Coquitlam, Port Coquitlam, Port Moody, Burnaby, Richmond, Surrey, "
        "New Westminster, Delta, Langley, ونکوور",
    ),
    ("DE", "Berlin", "chamedoon_berlin_khane", -1004440551125, "برلین"),
    ("DE", "Hamburg", "chamedoon_hamburg_khane", -1003833485673, "هامبورگ"),
    ("DE", "Cologne", "chamedoon_koln_khane", -1003213231959, "Köln, Koln, Koeln, کلن"),
]

# Sources of the six cities that were off until someone read a sample (2026-10-21: Mohammad turned them on).
ENABLE = [
    "Khanehamburg",
    "hamburg_khane",
    "koln_khane",
    "VancouverHousesAndApartments",
    "RentHomesVancouver",
    "vancouverhomesss",
    "EjareToronto",
    "Toronto_Iranians",
    "torontorentalmarket",
    "buy_renttoronto",
    "HouseToronto",
    "montrealhouses",
    "MontrealRentHome",
]

# A first read and the channel posts go back 10 days; older listings are not useful.
LOOKBACK_DAYS = 10


def seed(apps, schema_editor):
    HomeChannel = apps.get_model("home", "HomeChannel")
    HomeSource = apps.get_model("home", "HomeSource")
    HomeSettings = apps.get_model("home", "HomeSettings")
    for country, city, username, chat_id, aliases in CITY_CHANNELS:
        HomeChannel.objects.get_or_create(
            country=country,
            city=city,
            defaults={
                "username": username,
                "chat_id": chat_id,
                "aliases": aliases,
                "active_from": CUTOVER,
                "enabled": True,
            },
        )
    HomeSource.objects.filter(username__in=ENABLE).update(enabled=True)
    row, _created = HomeSettings.objects.get_or_create(pk=1)
    row.lookback_days = LOOKBACK_DAYS
    row.publish_window_days = LOOKBACK_DAYS
    row.save(update_fields=["lookback_days", "publish_window_days"])


def unseed(apps, schema_editor):
    HomeChannel = apps.get_model("home", "HomeChannel")
    HomeChannel.objects.filter(username__in=[row[2] for row in CITY_CHANNELS]).delete()
    apps.get_model("home", "HomeSource").objects.filter(username__in=ENABLE).update(enabled=False)


class Migration(migrations.Migration):
    dependencies = [("home", "0013_city_channels")]
    operations = [migrations.RunPython(seed, unseed)]
