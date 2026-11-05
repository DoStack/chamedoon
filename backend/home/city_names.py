"""City names: the Persian name of every city a listing can show, and the repair of what a post or the model wrote.

Rules only, no model. `home.review` reads this for new posts and `manage.py home_areas_sync` for old ones, so both give
the same answer. A name nobody listed stays as written: a wrong repair is worse than an odd spelling.
"""

from __future__ import annotations

from home.places import fold

# Folded English or German name to Persian. The launch cities and every city seen on production, 2026-11-04.
_NAMES = {
        # Canada
        "Toronto": "تورنتو",
        "Montreal": "مونترال",
        "Vancouver": "ونکوور",
        "Calgary": "کلگری",
        "Edmonton": "ادمونتون",
        "Winnipeg": "وینیپگ",
        "Ottawa": "اتاوا",
        "Kitchener": "کیچنر",
        "Barrie": "بری",
        "Sherbrooke": "شربروک",
        "Abbotsford": "ابوتسفورد",
        "Richmond Hill": "ریچموند هیل",
        "North York": "نورث یورک",
        "Thornhill": "تورنهیل",
        "Markham": "مارکهام",
        "Vaughan": "وان",
        "Newmarket": "نیومارکت",
        "Aurora": "آرورا",
        "Etobicoke": "اتوبیکوک",
        "Scarborough": "اسکاربرو",
        "Mississauga": "میسیساگا",
        "Brampton": "برمپتون",
        "Oakville": "اوک‌ویل",
        "Burlington": "برلینگتون",
        "Milton": "میلتون",
        "Pickering": "پیکرینگ",
        "Ajax": "آژاکس",
        "Whitby": "ویتبی",
        "Oshawa": "اوشاوا",
        "Holland Landing": "هالند لندینگ",
        "East Gwillimbury": "ایست گویلیمبری",
        "King City": "کینگ سیتی",
        "Stouffville": "استافویل",
        "Bradford": "بردفورد",
        "Keswick": "کزیک",
        "Sharon": "شارون",
        "North Vancouver": "نورث ونکوور",
        "West Vancouver": "وست ونکوور",
        "Coquitlam": "کوکیتلام",
        "Port Coquitlam": "پورت کوکیتلام",
        "Port Moody": "پورت مودی",
        "Burnaby": "برنابی",
        "Richmond": "ریچموند",
        "Surrey": "ساری",
        "New Westminster": "نیو وست‌مینستر",
        "Delta": "دلتا",
        "Langley": "لنگلی",
        "Maple Ridge": "میپل ریج",
        "Longueuil": "لانگوی",
        "Laval": "لاوال",
        "Brossard": "بروسارد",
        "Candiac": "کندیاک",
        "La Prairie": "لا پریری",
        # Germany and near it
        "Berlin": "برلین",
        "Hamburg": "هامبورگ",
        "Cologne": "کلن",
        "Munich": "مونیخ",
        "Frankfurt": "فرانکفورت",
        "Frankfurt (Oder)": "فرانکفورت (اودر)",
        "Dusseldorf": "دوسلدورف",
        "Essen": "اسن",
        "Nuremberg": "نورنبرگ",
        "Stuttgart": "اشتوتگارت",
        "Leipzig": "لایپزیگ",
        "Dortmund": "دورتموند",
        "Bremen": "برمن",
        "Hanover": "هانوفر",
        "Bonn": "بن",
        "Mannheim": "مانهایم",
        "Aachen": "آخن",
        "Mainz": "ماینتس",
        "Wiesbaden": "ویسبادن",
        "Darmstadt": "دارمشتات",
        "Freiburg": "فرایبورگ",
        "Augsburg": "آوگسبورگ",
        "Münster": "مونستر",
        "Bochum": "بوخوم",
        "Duisburg": "دویسبورگ",
        "Wuppertal": "ووپرتال",
        "Bielefeld": "بیله‌فلد",
        "Kassel": "کاسل",
        "Lübeck": "لوبک",
        "Magdeburg": "ماگدبورگ",
        "Halle": "هاله",
        "Potsdam": "پوتسدام",
        "Offenbach": "اوفنباخ",
        "Krefeld": "کرفلد",
        "Geesthacht": "گستاخت",
        "Pinneberg": "پینبرگ",
        "Ansbach": "آنسباخ",
        "Bamberg": "بامبرگ",
        "Würzburg": "وورتسبورگ",
        "Neuss": "نویس",
        "Siegen": "زیگن",
        "Henstedt-Ulzburg": "هنشتت-اولتسبورگ",
        "Bad Oldesloe": "باد اولدسلو",
        "Remscheid": "رمشاید",
        "Wanne-Eickel": "وانه-آیکل",
        "Jena": "ینا",
        "Innsbruck": "اینسبروک",
        "Wedel": "ودل",
        "Ingolstadt": "اینگولشتات",
        "Norderstedt": "نوردرشتت",
        "Erlangen": "ارلانگن",
        "Oberhausen": "اوبرهاوزن",
        "Wipperfürth": "ویپرفورت",
        "Rot": "روت",
        "Kaarst": "کارست",
        "Gießen": "گیسن",
        "Freising": "فرایزینگ",
        "Brannenburg": "برانن‌بورگ",
        "Boizenburg/Elbe": "بویتسنبورگ",
        "Rosenheim": "روزنهایم",
        "Tangstedt": "تانگشتت",
        "Cottbus": "کوتبوس",
        "Kelkheim": "کلکهایم",
        "Bad Vilbel": "باد فیلبل",
        "Erkrath": "ارکرات",
        "Mönchengladbach": "مونشن‌گلادباخ",
        "Pulheim": "پولهایم",
        "Frechen": "فرشن",
        "Hürth": "هورت",
        "Bergisch Gladbach": "برگیش گلادباخ",
        "Gelsenkirchen": "گلزنکیرشن",
        "Aschaffenburg": "آشافنبورگ",
        "Osnabrück": "اوسنابروک",
        "Bergheim": "برگهایم",
        "Großhansdorf": "گروس‌هانسدورف",
        "Siegburg": "زیگبورگ",
        "Worms": "ورمس",
        "Aßling": "آسلینگ",
        "Fürstenwalde": "فورستنوالده",
        "Lauenburg": "لاونبورگ",
        "Niederhöchstadt": "نیدرهوخشتات",
        "Bad Homburg": "باد هومبورگ",
        "Königs Wusterhausen": "کونیگس ووسترهاوزن",
        "Bernau bei Berlin": "برناو",
        "Bernau": "برناو",
        "Maintal": "ماینتال",
        "Bad Belzig": "باد بلتسیگ",
        "Erftstadt": "ارفتشتات",
        "Nagold": "ناگولد",
        "Düren": "دورن",
        "Krailling": "کرایلینگ",
        "Garching": "گارشینگ",
        "Buxtehude": "بوکستهوده",
        "Horrem": "هورم",
        "Sulzbach am Taunus": "زولتسباخ",
        "Erding": "اردینگ",
        "Dachau": "داخائو",
        "Hanau": "هاناو",
        "Neu Isenburg": "نویزنبورگ",
        "Geretsried": "گرتسرید",
        "Wentorf bei Hamburg": "ونتورف",
        "Seelze": "زلتسه",
        "Neu Wulmstorf": "نوی وولمزتورف",
        "Oberursel": "اوبرورزل",
        "Paderborn": "پادربورن",
        "Alfter": "آلفتر",
        "Rüsselsheim": "روسلسهایم",
        "Tornesch": "تورنش",
        "Zweibrücken": "تسوایبروکن",
        "Kamp-Lintfort": "کامپ-لینتفورت",
        "Hilden": "هیلدن",
        "Neuwied": "نویوید",
        "Prien am Chiemsee": "پرین",
        # Elsewhere
        "Yerevan": "ایروان",
}
# Read by folded name here and by lower-case name in `home.feed.city_names`.
CITY_FA = {key: fa for name, fa in _NAMES.items() for key in (fold(name), name.casefold())}

# What a post or the model wrote, folded, to the city it means.
SAME_CITY = {
    fold(name): city
    for name, city in {
        "Düsseldorf": "Dusseldorf",
        "Düsseldorf / Cologne": "Dusseldorf",
        "Giessen": "Gießen",
        "Friedberg, Gießen": "Gießen",
        "Luebeck": "Lübeck",
        "Lubeck": "Lübeck",
        "Branenburg": "Brannenburg",
        "Thornhill/Markham": "Thornhill",
        "Richmond Hill / Toronto": "Richmond Hill",
    }.items()
}
# A place of a metro written as the city: the metro, with the place as the area if the post has none.
PLACE_AS_CITY = {
    ("DE", "spandau"): ("Berlin", "Spandau"),
    ("DE", "mersan"): ("Berlin", "Marzahn"),
    ("DE", "sassel"): ("Hamburg", "Sasel"),
    ("DE", "vellinge"): ("Hamburg", "Wellingsbüttel"),
    ("CA", "oak ridges"): ("Richmond Hill", "Oak Ridges"),
}
# A city of another country filed under this one.
OTHER_COUNTRY = {("DE", "innsbruck"): "AT"}
# Another city written as the area of a post the source's city was filled in for.
ANOTHER_CITY = {
    ("DE", "cologne"): {
        fold(name): city
        for name, city in {
            "Dusseldorf": "Dusseldorf",
            "Düsseldorf": "Dusseldorf",
            "West Düsseldorf": "Dusseldorf",
            "Derdoseldorf": "Dusseldorf",
            "Essen": "Essen",
            "Hilden": "Hilden",
            "Düren": "Düren",
            "Krefeld city center": "Krefeld",
        }.items()
    },
}
# Not a place: what the model writes when the post names none.
NOT_A_PLACE = {"", "null", "none", "unknown", "n a", "na", "نامشخص", "best area", "best region"}


def is_place(text: str) -> bool:
    return fold(text) not in NOT_A_PLACE


def city_fa(name: str) -> str:
    """The city's Persian name, or blank when nobody has written one yet."""
    return CITY_FA.get(fold(name), "")


def clean_city(city: str, area: str, country: str, default_city: str = "") -> tuple[str, str, str]:
    """(city, area, country) with the dirty values repaired. A value with no rule comes back as it was."""
    area = area if is_place(area) else ""
    if not is_place(city):
        city = ""
    key = fold(city)
    country = OTHER_COUNTRY.get((country, key), country)
    if (country, key) in PLACE_AS_CITY:
        metro, place = PLACE_AS_CITY[(country, key)]
        return metro, area or place, country
    city = SAME_CITY.get(key, city)
    moved = ANOTHER_CITY.get((country, fold(city)), {}).get(fold(area))
    if moved:
        return moved, "", country
    if not city and fold(area) in CITY_FA:
        return SAME_CITY.get(fold(area), area), "", country
    return city or default_city, area, country
