"""Neighbourhood text for alerts: normalise, split, and place Toronto intersections in districts.

People type neighbourhoods in Latin or Persian, with or without accents, as one name,
several names ("Barmbek, Wandsbek یا Bramfeld") or a Toronto major intersection
("Yonge & Finch"). Listings carry the same mix in `area` and `city`. Both sides are
reduced to plain lower-case Latin, and an intersection becomes its district, so
"Yonge & Finch" and "Bayview and York Mills" are both North York.
"""

from __future__ import annotations

import re
import unicodedata

# Persian spellings of common districts and suburbs, as people type them.
FA_TO_LATIN = {
    # Toronto
    "نورث یورک": "north york",
    "نورت یورک": "north york",
    "ریچموند هیل": "richmond hill",
    "ریچمند هیل": "richmond hill",
    "اسکاربرو": "scarborough",
    "اسکاربورو": "scarborough",
    "اتوبیکوک": "etobicoke",
    "میسیساگا": "mississauga",
    "میسی‌ساگا": "mississauga",
    "داون تاون": "downtown",
    "داونتاون": "downtown",
    "مارکهام": "markham",
    "تورنهیل": "thornhill",
    "تورن هیل": "thornhill",
    "نیومارکت": "newmarket",
    "ویلودیل": "willowdale",
    "یانگ": "yonge",
    "فینچ": "finch",
    "شپرد": "sheppard",
    "استیلز": "steeles",
    "بیویو": "bayview",
    # Vancouver
    "برنابی": "burnaby",
    "نورث ونکوور": "north vancouver",
    "وست ونکوور": "west vancouver",
    "کوکیتلام": "coquitlam",
    "کوکویتلام": "coquitlam",
    "ریچموند": "richmond",
    # Montreal
    "لاوال": "laval",
    "بروسارد": "brossard",
    "برسارد": "brossard",
    "لانگوی": "longueuil",
    "کت د نژ": "cote-des-neiges",
    "کوت د نژ": "cote-des-neiges",
    "وردن": "verdun",
    "سن لوران": "saint-laurent",
    "وست مانت": "westmount",
    # Berlin
    "نویکلن": "neukolln",
    "نویکولن": "neukolln",
    "شارلوتنبورگ": "charlottenburg",
    "اشپاندائو": "spandau",
    "اشپانداو": "spandau",
    "مارتسان": "marzahn",
    "پانکو": "pankow",
    "ودینگ": "wedding",
    "میته": "mitte",
    "کرویتسبرگ": "kreuzberg",
    "اشتگلیتس": "steglitz",
    "لیشتنبرگ": "lichtenberg",
    # Hamburg
    "هاربورگ": "harburg",
    "ویلهلمزبورگ": "wilhelmsburg",
    "ویلهلمسبورگ": "wilhelmsburg",
    "وددل": "veddel",
    "آلتونا": "altona",
    "بارمبک": "barmbek",
    "واندسبک": "wandsbek",
    "برگدورف": "bergedorf",
    "آیمزبوتل": "eimsbuttel",
    "لانگنهورن": "langenhorn",
    # Cologne
    "کالک": "kalk",
    "نیپس": "nippes",
    "ارنفلد": "ehrenfeld",
    "لیندنتال": "lindenthal",
    "پورتس": "porz",
}

# Toronto: an intersection's district comes from its streets.
_SCARBOROUGH_NS = ("kennedy", "brimley", "mccowan", "markham rd", "morningside", "warden", "birchmount", "midland", "neilson", "port union", "scarborough town")
_ETOBICOKE_NS = ("islington", "kipling", "royal york", "martin grove", "the queensway", "rexdale", "albion", "humber")
_RICHMOND_HILL = ("elgin mills", "major mackenzie", "16th ave", "16th avenue", "19th ave", "hillcrest", "crosby", "gamble", "bernard", "jefferson", "oak ridges")
_NORTH_YORK_EW = ("steeles", "finch", "sheppard", "york mills", "cummer", "drewry", "wilson", "empress", "hendon", "willowdale", "bayview village", "fairview mall", "yorkdale", "don mills", "iranian plaza")
_DOWNTOWN = ("king st", "queen st", "dundas", "college", "front st", "union station", "spadina", "harbourfront", "yorkville", "chinatown", "kensington")
_MISSISSAUGA = ("square one", "hurontario", "erin mills", "port credit", "cooksville", "meadowvale", "heartland")
DISTRICTS = ("north york", "richmond hill", "downtown", "scarborough", "etobicoke", "mississauga")

_SEPARATORS = re.compile(r"\s*(?:[,،;|/]|\s+یا\s+|\s+و\s+|\s+or\s+|\s+oder\s+|\s+ou\s+)\s*")


def fold(text: str) -> str:
    """Lower case, no accents, ß as ss, Persian names as Latin, '&' as 'and', single spaces."""
    value = (text or "").replace("‌", " ").casefold()
    for persian, latin in sorted(FA_TO_LATIN.items(), key=lambda item: -len(item[0])):
        value = value.replace(persian, latin)
    value = value.replace("ß", "ss")
    value = "".join(ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch))
    value = value.replace("&", " and ").replace("+", " and ").replace("-", " ")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\bstreet\b", "st", value)
    value = re.sub(r"\bavenue\b", "ave", value)
    return re.sub(r"\s+", " ", value).strip()


def split_areas(text: str) -> list[str]:
    """The names a person typed: commas, slashes and «یا»/«و»/or separate them; '&' joins an intersection."""
    parts = _SEPARATORS.split(text or "")
    return [part.strip() for part in parts if part and part.strip()]


def toronto_district(text: str) -> str:
    """North York, Richmond Hill, Downtown, Scarborough, Etobicoke or Mississauga, or ''."""
    value = fold(text)
    if not value:
        return ""
    for name in DISTRICTS:
        if name in value:
            return name
    if any(street in value for street in _SCARBOROUGH_NS):
        return "scarborough"
    if any(street in value for street in _ETOBICOKE_NS):
        return "etobicoke"
    if any(street in value for street in _MISSISSAUGA):
        return "mississauga"
    if any(street in value for street in _RICHMOND_HILL):
        return "richmond hill"
    if any(street in value for street in _NORTH_YORK_EW):
        return "north york"
    if any(street in value for street in _DOWNTOWN):
        return "downtown"
    return ""


def area_matches(wanted: str, area: str, city: str, *, toronto: bool = False) -> bool:
    """Does a listing in `area`, `city` sit in any of the neighbourhoods the person typed?"""
    names = [fold(name) for name in split_areas(wanted)]
    names = [name for name in names if name]
    if not names:
        return True
    place = fold(f"{area} {city}")
    padded = f" {place} "
    listing_district = (toronto_district(area) or toronto_district(city)) if toronto else ""
    for name in names:
        if f" {name} " in padded or (len(name) >= 4 and name in place):
            return True
        if toronto:
            district = toronto_district(name)
            if district and district == listing_district:
                return True
    return False
