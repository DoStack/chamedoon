from __future__ import annotations

CATEGORIES: list[dict[str, object]] = [
    {"code": "DOCUMENTS", "name_en": "Documents", "name_fa": "مدارک", "sort_order": 1},
    {"code": "CLOTHES", "name_en": "Clothes", "name_fa": "لباس", "sort_order": 2},
    {"code": "PERSONAL_ITEMS", "name_en": "Personal items", "name_fa": "وسایل شخصی", "sort_order": 3},
    {"code": "ELECTRONICS", "name_en": "Electronics", "name_fa": "الکترونیک", "sort_order": 4},
    {"code": "FOOD", "name_en": "Food", "name_fa": "مواد غذایی", "sort_order": 5},
    {"code": "MEDICINE", "name_en": "Medicine", "name_fa": "دارو", "sort_order": 6},
    {"code": "CIGARETTES", "name_en": "Cigarettes", "name_fa": "سیگار", "sort_order": 7},
    {"code": "FRAGILE", "name_en": "Fragile", "name_fa": "شکستنی", "sort_order": 8},
    {"code": "PET", "name_en": "Pet", "name_fa": "حیوان خانگی", "sort_order": 9},
    {"code": "OTHER", "name_en": "Other", "name_fa": "سایر", "sort_order": 10},
]

COUNTRIES: list[dict[str, str]] = [
    {"code": "IR", "name_en": "Iran", "name_fa": "ایران"},
    {"code": "CA", "name_en": "Canada", "name_fa": "کانادا"},
    {"code": "US", "name_en": "United States", "name_fa": "آمریکا"},
    {"code": "GB", "name_en": "United Kingdom", "name_fa": "بریتانیا"},
    {"code": "DE", "name_en": "Germany", "name_fa": "آلمان"},
    {"code": "TR", "name_en": "Turkey", "name_fa": "ترکیه"},
    {"code": "AE", "name_en": "United Arab Emirates", "name_fa": "امارات"},
    {"code": "AM", "name_en": "Armenia", "name_fa": "ارمنستان"},
    {"code": "IQ", "name_en": "Iraq", "name_fa": "عراق"},
    {"code": "FR", "name_en": "France", "name_fa": "فرانسه"},
    {"code": "NL", "name_en": "Netherlands", "name_fa": "هلند"},
    {"code": "AT", "name_en": "Austria", "name_fa": "اتریش"},
    {"code": "SE", "name_en": "Sweden", "name_fa": "سوئد"},
    {"code": "NO", "name_en": "Norway", "name_fa": "نروژ"},
    {"code": "DK", "name_en": "Denmark", "name_fa": "دانمارک"},
    {"code": "CH", "name_en": "Switzerland", "name_fa": "سوئیس"},
]

CITIES: list[dict[str, str]] = [
    {"country": "IR", "slug": "tehran", "name_en": "Tehran", "name_fa": "تهران"},
    {"country": "IR", "slug": "mashhad", "name_en": "Mashhad", "name_fa": "مشهد"},
    {"country": "IR", "slug": "isfahan", "name_en": "Isfahan", "name_fa": "اصفهان"},
    {"country": "IR", "slug": "shiraz", "name_en": "Shiraz", "name_fa": "شیراز"},
    {"country": "IR", "slug": "tabriz", "name_en": "Tabriz", "name_fa": "تبریز"},
    {"country": "CA", "slug": "toronto", "name_en": "Toronto", "name_fa": "تورنتو"},
    {"country": "CA", "slug": "vancouver", "name_en": "Vancouver", "name_fa": "ونکوور"},
    {"country": "CA", "slug": "montreal", "name_en": "Montreal", "name_fa": "مونترال"},
    {"country": "CA", "slug": "ottawa", "name_en": "Ottawa", "name_fa": "اتاوا"},
    {"country": "CA", "slug": "calgary", "name_en": "Calgary", "name_fa": "کلگری"},
    {"country": "US", "slug": "new-york", "name_en": "New York", "name_fa": "نیویورک"},
    {"country": "US", "slug": "los-angeles", "name_en": "Los Angeles", "name_fa": "لس‌آنجلس"},
    {"country": "US", "slug": "washington-dc", "name_en": "Washington DC", "name_fa": "واشنگتن دی‌سی"},
    {"country": "US", "slug": "chicago", "name_en": "Chicago", "name_fa": "شیکاگو"},
    {"country": "GB", "slug": "london", "name_en": "London", "name_fa": "لندن"},
    {"country": "GB", "slug": "manchester", "name_en": "Manchester", "name_fa": "منچستر"},
    {"country": "DE", "slug": "berlin", "name_en": "Berlin", "name_fa": "برلین"},
    {"country": "DE", "slug": "frankfurt", "name_en": "Frankfurt", "name_fa": "فرانکفورت"},
    {"country": "DE", "slug": "hamburg", "name_en": "Hamburg", "name_fa": "هامبورگ"},
    {"country": "DE", "slug": "munich", "name_en": "Munich", "name_fa": "مونیخ"},
    {"country": "TR", "slug": "istanbul", "name_en": "Istanbul", "name_fa": "استانبول"},
    {"country": "TR", "slug": "ankara", "name_en": "Ankara", "name_fa": "آنکارا"},
    {"country": "AE", "slug": "dubai", "name_en": "Dubai", "name_fa": "دبی"},
    {"country": "AE", "slug": "abu-dhabi", "name_en": "Abu Dhabi", "name_fa": "ابوظبی"},
    {"country": "AM", "slug": "yerevan", "name_en": "Yerevan", "name_fa": "ایروان"},
    {"country": "IQ", "slug": "baghdad", "name_en": "Baghdad", "name_fa": "بغداد"},
    {"country": "IQ", "slug": "erbil", "name_en": "Erbil", "name_fa": "اربیل"},
    {"country": "FR", "slug": "paris", "name_en": "Paris", "name_fa": "پاریس"},
    {"country": "NL", "slug": "amsterdam", "name_en": "Amsterdam", "name_fa": "آمستردام"},
    {"country": "AT", "slug": "vienna", "name_en": "Vienna", "name_fa": "وین"},
    {"country": "SE", "slug": "stockholm", "name_en": "Stockholm", "name_fa": "استکهلم"},
    {"country": "NO", "slug": "oslo", "name_en": "Oslo", "name_fa": "اسلو"},
    {"country": "DK", "slug": "copenhagen", "name_en": "Copenhagen", "name_fa": "کپنهاگ"},
    {"country": "CH", "slug": "zurich", "name_en": "Zurich", "name_fa": "زوریخ"},
]


def seed_catalog() -> None:
    from item_requests.models import Category, City, Country

    for item in CATEGORIES:
        Category.objects.update_or_create(
            code=item["code"],
            defaults={
                "name_en": item["name_en"],
                "name_fa": item["name_fa"],
                "sort_order": item["sort_order"],
                "is_active": True,
            },
        )
    for item in COUNTRIES:
        Country.objects.update_or_create(
            code=item["code"],
            defaults={
                "name_en": item["name_en"],
                "name_fa": item["name_fa"],
                "is_active": True,
            },
        )
    for item in CITIES:
        country = Country.objects.get(code=item["country"])
        City.objects.update_or_create(
            country=country,
            slug=item["slug"],
            defaults={
                "name_en": item["name_en"],
                "name_fa": item["name_fa"],
                "is_active": True,
            },
        )
