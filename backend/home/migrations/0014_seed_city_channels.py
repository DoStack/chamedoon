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

# Persian chats of the six cities found by Telegram search on 2026-10-22 (read-only scan:
# members, posts in 30 days, posts with a housing word). Seeded OFF: switch each on in
# Admin → Home sources after a look. username, title, kind, country, city, region.
CANDIDATES = [
    ("TORONTO_NEEDS", "🇨🇦 نیازمندیهای تورنتو🇨🇦", "group", "CA", "Toronto", "ON"),  # 5139 members, 2238 housing posts/30d
    ("KhaneyabToronto", "خانه یاب تورنتو", "group", "CA", "Toronto", "ON"),  # 1017 members, 1706 housing posts/30d
    ("RichmondHillToronto", "نیازمندیهای تورنتو و ریچموندهیل کانادا", "group", "CA", "Toronto", "ON"),  # 5451 members, 1187 housing posts/30d
    ("Toronto_home_commercial", "نیازمندیهای تورنتو", "group", "CA", "Toronto", "ON"),  # 1606 members, 999 housing posts/30d
    ("Toronto_Iranian", "🇨🇦بیلبورد ایرانیان تورنتو🇨🇦", "group", "CA", "Toronto", "ON"),  # 2616 members, 775 housing posts/30d
    ("Toronto_Ontario", "نیازمندیهای ایرانیان تورنتو کانادا", "group", "CA", "Toronto", "ON"),  # 5517 members, 733 housing posts/30d
    ("TorontoNiazmandiha", "🔔نیازمندی های تورنتو🔔", "group", "CA", "Toronto", "ON"),  # 2745 members, 309 housing posts/30d
    ("rentalstoronto", "اجاره اتاق و مسکن تورنتو", "group", "CA", "Toronto", "ON"),  # 1448 members, 227 housing posts/30d
    ("luxarytorontohome", "خرید و فروش و اجاره و سابلت خانه در تورنتو Luxary Toronto Home", "group", "CA", "Toronto", "ON"),  # 545 members, 247 housing posts/30d
    ("montrealiran", "نیازمندیهای ایرانیان مونترال", "group", "CA", "Montreal", "QC"),  # 2271 members, 98 housing posts/30d
    ("whmfi", "ایرانیان بریتیش کلمبیا,ونکوور", "group", "CA", "Vancouver", "BC"),  # 4962 members, 684 housing posts/30d
    ("ResidentIraniansOfVacouver", "ایرانیان مقیم ونکوور کانادا", "group", "CA", "Vancouver", "BC"),  # 13397 members, 402 housing posts/30d
    ("RealEstate_BC", "نیازمندیهای ایرانیان ونکوور", "group", "CA", "Vancouver", "BC"),  # 10577 members, 354 housing posts/30d
    ("VancouverIranian", "کاریابی و نیازمندیهای ایرانیان ونکوور", "group", "CA", "Vancouver", "BC"),  # 10422 members, 209 housing posts/30d
    ("vancouverniaz", "🇨🇦نیازمندیهای ونکوور🇨🇦", "group", "CA", "Vancouver", "BC"),  # 2426 members, 148 housing posts/30d
    ("Vancouver_work_house", "تبلیغ کار، خانه و اسکان در‌ ونکوور", "group", "CA", "Vancouver", "BC"),  # 713 members, 142 housing posts/30d
    ("DivarVancouver", "🎯 نیازمندیهای ایرانیان ونکوور 🎯", "group", "CA", "Vancouver", "BC"),  # 3492 members, 119 housing posts/30d
    ("BerlinWohnungNews", "Berlin Wohnung News | خانه در برلین", "channel", "DE", "Berlin", ""),  # 390 members, 1485 housing posts/30d
    ("iranian_berlin", "ایرانیان برلین", "group", "DE", "Berlin", ""),  # 16446 members, 199 housing posts/30d
    ("BerlinHilfe", "🇩🇪نیازمندی های برلین و برندنبورگ🇩🇪", "group", "DE", "Berlin", ""),  # 4536 members, 122 housing posts/30d
    ("niyazmandiberlin", "نیازمندیهای برلین و برندنبورگ", "group", "DE", "Berlin", ""),  # 427 members, 84 housing posts/30d
    ("niyazmandihamburg", "نیازمندی هامبورگ", "group", "DE", "Hamburg", ""),  # 1750 members, 183 housing posts/30d
    ("iranian_hamburg", "ایرانیان هامبورگ ، هانوفر ، برمن", "group", "DE", "Hamburg", ""),  # 11348 members, 182 housing posts/30d
    ("divareiranianehamburg", "دیوار ایرانیان هامبورگ", "group", "DE", "Hamburg", ""),  # 330 members, 80 housing posts/30d
    ("almankoln", "نیازمندیهای ایرانیان کلن، دوسلدورف ، اسن، ووپرتال،بوخوم،دوویسبورگ مونشنگلادباخ و... köln Düsseldorf (NRW)", "group", "DE", "Cologne", ""),  # 10297 members, 338 housing posts/30d
    ("iranian_koln", "ایرانیان کلن، دوسلدورف، آخن", "group", "DE", "Cologne", ""),  # 18664 members, 275 housing posts/30d
    ("niyazmandikoln", "نیازمندیهای هامبورگ کلن دوسلدورف", "group", "DE", "Cologne", ""),  # 1095 members, 262 housing posts/30d
    ("KoelnDuesseldorf", "گروه ایرانیان کلن دوسلدورف", "group", "DE", "Cologne", ""),  # 3068 members, 169 housing posts/30d
    ("godparticles1", "اطلس طلایی ایرانیان کلن،دوسلدورف و ایرانیان شرق المان", "group", "DE", "Cologne", ""),  # 1427 members, 176 housing posts/30d
]
CURRENCY = {"CA": "CAD", "DE": "EUR"}

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
    for username, title, kind, country, city, region in CANDIDATES:
        HomeSource.objects.get_or_create(
            username=username,
            defaults={
                "title": title,
                "kind": kind,
                "country": country,
                "default_city": city,
                "default_region": region,
                "default_currency": CURRENCY[country],
                "enabled": False,
            },
        )
    row, _created = HomeSettings.objects.get_or_create(pk=1)
    row.lookback_days = LOOKBACK_DAYS
    row.publish_window_days = LOOKBACK_DAYS
    row.save(update_fields=["lookback_days", "publish_window_days"])


def unseed(apps, schema_editor):
    HomeChannel = apps.get_model("home", "HomeChannel")
    HomeChannel.objects.filter(username__in=[row[2] for row in CITY_CHANNELS]).delete()
    HomeSource = apps.get_model("home", "HomeSource")
    HomeSource.objects.filter(username__in=[row[0] for row in CANDIDATES]).delete()
    HomeSource.objects.filter(username__in=ENABLE).update(enabled=False)


class Migration(migrations.Migration):
    dependencies = [("home", "0013_city_channels")]
    operations = [migrations.RunPython(seed, unseed)]
