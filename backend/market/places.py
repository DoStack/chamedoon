from __future__ import annotations

import re

PLACES: list[tuple[str, str, tuple[str, ...]]] = [
    ("Tehran", "IR", ("تهران", "tehran")),
    ("Karaj", "IR", ("کرج", "karaj")),
    ("Mashhad", "IR", ("مشهد", "mashhad", "mashad")),
    ("Isfahan", "IR", ("اصفهان", "isfahan", "esfahan")),
    ("Shiraz", "IR", ("شیراز", "shiraz")),
    ("Tabriz", "IR", ("تبریز", "tabriz")),
    ("Sari", "IR", ("ساری",)),
    ("Rasht", "IR", ("رشت",)),
    ("Yazd", "IR", ("یزد",)),
    ("Kish", "IR", ("کیش",)),
    ("Iran", "IR", ("ایران", "iran")),
    ("Toronto", "CA", ("تورنتو", "toronto", "تورونتو", "north york", "ریچموندهیل")),
    ("Vancouver", "CA", ("ونکوور", "vancouver")),
    ("Montreal", "CA", ("مونترال", "montreal", "مونترآل")),
    ("Ottawa", "CA", ("اتاوا", "ottawa")),
    ("Calgary", "CA", ("کلگری", "calgary")),
    ("Canada", "CA", ("کانادا", "canada")),
    ("Los Angeles", "US", ("لس آنجلس", "لس‌آنجلس", "لس انجلس", "los angeles")),
    ("New York", "US", ("نیویورک", "new york")),
    ("Chicago", "US", ("شیکاگو", "chicago")),
    ("Dallas", "US", ("دالاس", "dallas")),
    ("United States", "US", ("آمریکا", "امریکا", "america")),
    ("London", "GB", ("لندن", "london")),
    ("Manchester", "GB", ("منچستر", "manchester")),
    ("United Kingdom", "GB", ("انگلیس", "انگلستان", "بریتانیا")),
    ("Berlin", "DE", ("برلین", "berlin")),
    ("Frankfurt", "DE", ("فرانکفورت", "frankfurt")),
    ("Hamburg", "DE", ("هامبورگ", "hamburg")),
    ("Munich", "DE", ("مونیخ", "munich")),
    ("Cologne", "DE", ("کلن", "cologne")),
    ("Nuremberg", "DE", ("نورنبرگ", "nuremberg")),
    ("Dusseldorf", "DE", ("دوسلدورف", "دوسلدرف", "dusseldorf")),
    ("Hanover", "DE", ("هانوفر", "هانوور", "هانور", "hanover", "hannover")),
    ("Augsburg", "DE", ("آگزبورگ", "آگسبورگ", "اگزبورگ", "augsburg")),
    ("Germany", "DE", ("آلمان", "germany", "المان")),
    ("Istanbul", "TR", ("استانبول", "istanbul")),
    ("Ankara", "TR", ("آنکارا", "ankara")),
    ("Turkey", "TR", ("ترکیه", "turkey")),
    ("Dubai", "AE", ("دبی", "dubai")),
    ("Abu Dhabi", "AE", ("ابوظبی",)),
    ("Yerevan", "AM", ("ایروان", "yerevan")),
    ("Armenia", "AM", ("ارمنستان", "armenia")),
    ("Paris", "FR", ("پاریس", "paris")),
    ("France", "FR", ("فرانسه", "france")),
    ("Amsterdam", "NL", ("آمستردام", "amsterdam")),
    ("Netherlands", "NL", ("هلند", "netherlands")),
    ("Vienna", "AT", ("وین", "vienna")),
    ("Austria", "AT", ("اتریش", "austria")),
    ("Stockholm", "SE", ("استکهلم", "stockholm")),
    ("Sweden", "SE", ("سوئد", "sweden")),
    ("Oslo", "NO", ("اسلو", "oslo")),
    ("Copenhagen", "DK", ("کپنهاگ", "copenhagen")),
    ("Denmark", "DK", ("دانمارک", "denmark")),
    ("Zurich", "CH", ("زوریخ", "zurich")),
    ("Switzerland", "CH", ("سوئیس", "switzerland")),
    ("Milan", "IT", ("میلان", "milan")),
    ("Rome", "IT", ("#رم", "به رم", "از رم", "مقصد رم", "مبدا رم", "مقصد: رم", "مبدا: رم", " rome")),
    ("Turin", "IT", ("تورین", "turin")),
    ("Naples", "IT", ("ناپل", "ناپلی", "ناپولی", "naples")),
    ("Bologna", "IT", ("بلونیا", "بولونیا", "bologna")),
    ("Forli", "IT", ("فورلی", "فولی", "forli", "forlì")),
    ("Rimini", "IT", ("ریمینی", "rimini")),
    ("Italy", "IT", ("ایتالیا", "italy")),
    ("Barcelona", "ES", ("بارسلونا", "barcelona")),
    ("Spain", "ES", ("اسپانیا", "spain")),
    ("Brussels", "BE", ("بروکسل", "brussels")),
    ("Belgium", "BE", ("بلژیک", "belgium")),
]

_ALIASES: list[tuple[str, str, str, int]] = []
for _name, _cc, _aliases in PLACES:
    for _alias in _aliases:
        _ALIASES.append((_alias.lower().replace("‌", ""), _name, _cc, len(_alias)))
_ALIASES.sort(key=lambda item: -item[3])


def find_parenthetical_cities(text: str) -> list[dict[str, str]]:
    from market.catalog import COUNTRY_ONLY

    haystack = text or ""
    found: list[dict[str, str]] = []
    for name, country, aliases in PLACES:
        if name not in COUNTRY_ONLY:
            continue
        for alias in aliases:
            pattern = rf"{re.escape(alias)}\s*[\(\uff08]\s*([^\)\uff09]{{1,40}})\s*[\)\uff09]"
            for match in re.finditer(pattern, haystack, flags=re.I):
                inner = (match.group(1) or "").strip()
                if not inner:
                    continue
                known = find_places(inner)
                cities = [place for place in known if place.get("city") not in COUNTRY_ONLY]
                if cities:
                    found.extend(cities)
                    continue
                if known:
                    continue
                found.append({"city": inner, "country": country})
    seen: list[dict[str, str]] = []
    for place in found:
        if place not in seen:
            seen.append(place)
    return seen


def find_places(text: str) -> list[dict[str, str]]:
    haystack = text.lower().replace("‌", "").replace("ـ", "")
    found: list[tuple[int, str, str]] = []
    used: list[tuple[int, int]] = []
    for alias, name, country, _length in _ALIASES:
        start = 0
        while True:
            index = haystack.find(alias, start)
            if index < 0:
                break
            end = index + len(alias)
            if any(index < used_end and end > used_start for used_start, used_end in used):
                start = index + 1
                continue
            found.append((index, name, country))
            used.append((index, end))
            start = end
    found.sort()
    seen: set[tuple[str, str]] = set()
    places: list[dict[str, str]] = []
    for _index, name, country in found:
        key = (name, country)
        if key in seen:
            continue
        seen.add(key)
        places.append({"city": name, "country": country})
    return places
