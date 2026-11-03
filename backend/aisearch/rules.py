"""Rules first: turn a Persian, English or mixed query into a SearchSpec with no model call.

Each rule cuts what it understood out of the text, leaving a `|` so that what remains on either side never joins into a
false name. What is left at the end is the only thing the model is ever asked about. A wish with no filter behind it
(«پارکینگ») goes straight to `ignored`, since no model could help with that.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta

from aisearch.spec import MAX_NAMES, SearchSpec, toggle_allowed
from home import deeplink
from home.areas import _metro_key, _search_key, area_names
from home.feed import city_names
from home.models import HomeArea, HomeChannel
from home.places import fold, toronto_district

# Part of the cache key: a change to the rules below must not serve answers of the old ones.
RULES_VERSION = "1"

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_LETTERS = str.maketrans({"ي": "ی", "ى": "ی", "ئ": "ی", "ك": "ک", "ة": "ه", "‌": " ", "‏": " ", "‎": " ", "ـ": ""})
_NUM_WORDS = {"یک": 1, "یه": 1, "دو": 2, "سه": 3, "چهار": 4, "پنج": 5, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ein": 1, "zwei": 2, "drei": 3, "vier": 4}
_NUM_WORD = "|".join(_NUM_WORDS)


def normalize(text: str) -> str:
    """One spelling per query: the cache key, and what the rules read. Digits ASCII, one form of each letter, no thousands mark."""
    value = unicodedata.normalize("NFKC", text or "").translate(_DIGITS).translate(_LETTERS).lower()
    value = re.sub(r"(?<=\d)[٬,'’](?=\d{3}\b)", "", value)
    value = value.replace("$", " ").replace("€", " ").replace("&", " و ").replace("+", " + ")
    value = value.replace("½", " 1/2")
    value = re.sub(r"[،,؛;:!؟?()\[\]«»\"*#]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


# ---------------------------------------------------------------------------
# Places


_CITY_FA = {"تورنتو": "toronto", "تورونتو": "toronto", "مونترال": "montreal", "مونترآل": "montreal", "مونرال": "montreal", "ونکوور": "vancouver", "ونکور": "vancouver", "برلین": "berlin", "هامبورگ": "hamburg", "کلن": "cologne", "köln": "cologne", "koln": "cologne"}
_FA_OF_METRO = {"toronto": "تورنتو", "montreal": "مونترال", "vancouver": "ونکوور", "berlin": "برلین", "hamburg": "هامبورگ", "cologne": "کلن"}
MIN_NAME = 3
MAX_SUGGESTIONS = 3


def _titled(phrase: str) -> str:
    return " ".join(word.capitalize() if word.isascii() else word for word in phrase.split())


def _is_street_phrase(key: str) -> bool:
    """`toronto_district` looks for a street anywhere in a text. Here the words around it must not be swallowed with it:
    one word, a two-word name that only means a street whole («york mills»), or two of those joined at an intersection."""
    parts = key.split(" and ")
    if len(parts) > 2 or any(len(part.split()) > 2 for part in parts):
        return False
    if len(parts) == 2:
        return True
    words = key.split()
    return len(words) == 1 or not any(toronto_district(word) for word in words)


@dataclass
class Metro:
    """What a text can name, from the city being browsed. `here` is None when no city is chosen yet."""

    here: HomeChannel | None
    country: str
    rows_here: list[HomeArea] = field(default_factory=list)
    rows_elsewhere: list[HomeArea] = field(default_factory=list)
    cities: dict[str, HomeChannel] = field(default_factory=dict)
    names_here: dict[str, HomeArea] = field(default_factory=dict)
    names_elsewhere: dict[str, HomeArea] = field(default_factory=dict)

    @classmethod
    def load(cls, here: HomeChannel | None, country: str = "") -> "Metro":
        here_key = (here.country, _metro_key(here.city)) if here else None
        metro = cls(here=here, country=here.country if here else country)
        for row in HomeArea.objects.all():
            if (row.country, _metro_key(row.city)) == here_key:
                metro.rows_here.append(row)
                for name in area_names(row):
                    metro.names_here.setdefault(name, row)
            else:
                metro.rows_elsewhere.append(row)
                metro.names_elsewhere.setdefault(fold(row.canonical), row)
                metro.names_elsewhere.setdefault(fold(row.fa), row)
        for channel in HomeChannel.objects.filter(enabled=True).exclude(city=""):
            metro.cities[_metro_key(channel.city)] = channel
        return metro

    @property
    def is_toronto(self) -> bool:
        return bool(self.here) and _metro_key(self.here.city) == "toronto"

    def is_quebec(self) -> bool:
        return bool(self.here) and _metro_key(self.here.city) == "montreal"

    def area_here(self, phrase: str) -> tuple[HomeArea, str] | None:
        """The area of this city a phrase names, and the label of its chip."""
        key = _search_key(phrase)
        if len(key) < MIN_NAME:
            return None
        row = self.names_here.get(key)
        if row:
            # Its own name reads as the area; an intersection or other name reads as the person wrote it.
            own = key in {fold(row.canonical), fold(row.fa)}
            return row, row.fa if own else _titled(phrase)
        if self.is_toronto and _is_street_phrase(key):
            district = toronto_district(key)
            if district and (district != "downtown" or "downtown" in key):
                row = next((item for item in self.rows_here if item.canonical == district), None)
                if row:
                    return row, _titled(phrase)
        return None

    def city_named(self, phrase: str) -> HomeChannel | None:
        return self.cities.get(_metro_key(_CITY_FA.get(phrase, phrase)))

    def city_of_area_elsewhere(self, phrase: str) -> HomeChannel | None:
        key = _search_key(phrase)
        if len(key) < MIN_NAME:
            return None
        row = self.names_elsewhere.get(key)
        return self.cities.get(_metro_key(row.city)) if row else None

    def suggestions(self, phrase: str) -> list[dict]:
        """F08#6: the nearest names, then this city's first areas. The client adds «بدون محله»."""
        close = difflib.get_close_matches(fold(phrase), list(self.names_here), n=MAX_SUGGESTIONS, cutoff=0.6)
        rows = list(dict.fromkeys(self.names_here[name].pk for name in close))
        for row in self.rows_here:
            if len(rows) >= MAX_SUGGESTIONS:
                break
            if row.pk not in rows:
                rows.append(row.pk)
        named = {row.pk: row for row in self.rows_here}
        return [{"id": pk, "label": named[pk].fa, "name": named[pk].fa} for pk in rows]


def other_city_row(channel: HomeChannel) -> dict:
    key = _metro_key(channel.city)
    names = city_names()
    return {"slug": deeplink.slug(channel.city), "name_fa": names.get(channel.city.casefold()) or _FA_OF_METRO.get(key, channel.city), "country": channel.country}


# ---------------------------------------------------------------------------
# The rules


_MONTHS = {
    1: "ژانویه|ژانویهٔ|جنوری|january|januar|jan",
    2: "فوریه|فبریه|february|februar|feb",
    3: "مارس|مارچ|march|märz|mar",
    4: "آوریل|اپریل|april|apr",
    5: "مه|may|mai",
    6: "ژوئن|جون|june|juni|jun",
    7: "ژوئیه|جولای|july|juli|jul",
    8: "اوت|آگوست|اگوست|august|aug",
    9: "سپتامبر|september|sept|sep",
    10: "اکتبر|october|oktober|oct|okt",
    11: "نوامبر|november|nov",
    12: "دسامبر|december|dezember|dec|dez",
}
_MONTH_OF = {name: number for number, names in _MONTHS.items() for name in names.split("|")}
# Words that can be something else unless a day stands next to them.
_NEEDS_DAY = {"مه", "may", "mai", "mar", "jun", "jul", "sep", "dec", "jan", "apr", "aug", "nov", "oct", "feb"}
_MONTH = "|".join(sorted(_MONTH_OF, key=len, reverse=True))
_DAY_WORDS = {"اول": 1, "اوایل": 1, "وسط": 15, "نیمه": 15, "اواسط": 15, "آخر": 28, "اواخر": 28, "early": 1, "mid": 15, "late": 28, "end": 28, "anfang": 1, "mitte": 15, "ende": 28}
_DAY = r"\d{1,2}|" + "|".join(_DAY_WORDS)
_DATE = re.compile(
    rf"(?P<prefix>تا قبل از|قبل از|تا|از|by|before|bis|from|since|ab|starting)?\s*\b"
    rf"(?:(?:(?P<day>{_DAY})\.?\s*(?:ام|th|st|nd|rd|of)?\s+)(?P<month>{_MONTH})|(?P<month2>{_MONTH})(?:\s+(?P<day2>\d{{1,2}})(?:th|st|nd|rd)?)?)\b(?!\s*\d{{3}})"
)
_SOON = re.compile(r"\b(فوری|همین الان|همین حالا|immediately|asap|sofort|right away)\b")
SOON_DAYS = 14

_BEDS = re.compile(rf"(?<![\d.])(?P<n>\d|{_NUM_WORD})\s*-?\s*(?:اتاق خوابه|اتاق خواب|خوابه|خوابی|خواب|bedrooms?|beds?|bdrm|br|bd|zimmer|zi|اتاقه)\b")
_PLUS_DEN = re.compile(r"(?<![\d.])(?P<n>[1-4])\s*\+\s*(?:1|den|دن)\b")
_QUEBEC_SIZE = re.compile(r"(?<![\d.])[1-8]\s*1/2(?!\w)")
_SIZE = re.compile(r"(?<![\d.])(?P<n>\d{2,4})\s*(?:متر مربع|مترمربع|متری|متر|m2|m²|sqm|qm|square meters?)(?!\w)")
_SQFT = re.compile(r"(?<![\d.])(?P<n>\d{3,5})\s*(?:sqft|sq ft|square feet|فوت)(?!\w)")
_THOUSANDS = re.compile(rf"(?<![\d.])(?P<n>\d+(?:\.\d+)?|{_NUM_WORD})\s*(?:k|هزار|tausend)(?:\s+و\s+(?P<rest>\d{{3}}|پانصد))?(?!\w)")

_N = r"(\d{3,6})"
_MONEY = r"(?:\s*(?:دلار|یورو|تومن|cad|usd|eur|euro|euros|dollars?|cash))?"
_RANGE = re.compile(rf"(?:\b(?:از|بین|between|from|von)\s+)?(?<![\d.]){_N}{_MONEY}\s*(?:تا|الی|to|and|bis|-|–)\s*{_N}{_MONEY}(?!\w)")
_BETWEEN = re.compile(rf"\b(?:بین|between)\s+{_N}{_MONEY}\s+(?:و|and)\s+{_N}{_MONEY}(?!\w)")
_MAX = re.compile(rf"(?:\b(?:تا|زیر|کمتر از|حداکثر|نهایتا|ماکزیمم|سقف|under|below|less than|max|maximum|up to|bis|unter)\s+|<\s*){_N}{_MONEY}(?!\w)")
_MAX_AFTER = re.compile(rf"(?<![\d.]){_N}{_MONEY}\s+(?:یا کمتر|به پایین|or less|or under|max)\b")
_MIN = re.compile(rf"(?:\b(?:از|بالای|بیشتر از|حداقل|مینیمم|over|above|min|minimum|at least|from|ab|über)\s+|>\s*){_N}{_MONEY}(?!\w)")
_ABOUT = re.compile(rf"(?:\b(?:حدود|حدودا|دور و بر|تقریبا|around|about|approx|circa|etwa)\s+|~\s*)?(?<![\d.]){_N}{_MONEY}(?!\w)")
PRICE_LOW = 100
PRICE_HIGH = 20_000
BARE_PRICE_LOW = 300

# Longest words first where one holds another. Bedrooms are read before these, so «اتاق خواب» is not a room.
_TYPES = (
    ("room", re.compile(r"\b(هم خانه|هم خونه|همخانه|همخونه|هم اتاقی|شیرینگ|اتاق|roommate|shared room|room|wg zimmer|wg|mitbewohner)\b")),
    ("studio", re.compile(r"\b(استودیو|استدیو|بچلر|studio|bachelor|einzimmerwohnung)\b")),
    ("basement", re.compile(r"\b(بیسمنت|بیس منت|بیسمن|زیر زمین|زیرزمین|basement|souterrain)\b")),
    ("apartment", re.compile(r"\b(آپارتمان|اپارتمان|کاندو|apartment|apt|condo|flat|wohnung)\b")),
    ("house", re.compile(r"\b(خانه کامل|خونه کامل|خانه ویلایی|ویلایی|ویلا|تاون هوس|تاونهوس|townhouse|house|haus)\b")),
)
_WANTED = re.compile(r"\b(آگهی مستاجر|مستاجر|tenant wanted|wanted|gesuch)\b")
_TERMS = (
    ("lease_transfer", re.compile(r"\b(انتقال لیز|واگذاری لیز|lease transfer|lease takeover|nachmieter)\b")),
    ("sublet", re.compile(r"\b(سابلت|سب لت|ساب لت|sublet|zwischenmiete|untermiete)\b")),
    ("short", re.compile(r"\b(کوتاه مدت|کوتاه|short term|short stay|kurzzeit)\b")),
    ("lease", re.compile(r"\b(بلند مدت|بلندمدت|long term|langfristig|لیز)\b")),
)
_GENDERS = (
    ("couple", re.compile(r"\b(مناسب زوج|زوج|couples?|paar)\b")),
    ("female", re.compile(r"\b(فقط خانم|مناسب خانم|فقط بانو|مخصوص خانم|خانم ها|خانم|بانو|دختر|female|women|woman|girls?|frauen|frau)\b")),
    ("male", re.compile(r"\b(فقط آقا|مناسب آقا|مخصوص آقا|فقط اقا|آقایان|آقا|اقا|پسر|male|men|man|boys?|männer|mann)\b")),
)
# A wish no filter stands behind, cut first so «بدون مبله» does not read as furnished.
_NO_FILTER = re.compile(r"\b(بدون مبله|غیر مبله|غیرمبله|unfurnished|unmöbliert|پارکینگ|parking|لاندری|لباسشویی|laundry|بالکن|تراس|balcony|آسانسور|elevator|حیوان|پت|pets?|سگ|گربه|استخر|pool|باشگاه|gym|سیگار|smoking|نوساز|تمیز|دنج|آفتابگیر|ساکت|bright|sunny|quiet|clean|cozy|cosy)\b")
_TOGGLES = (
    ("nowbs", re.compile(r"\b(بدون wbs|ohne wbs|no wbs|wbs nicht|wbs frei)\b")),
    ("furnished", re.compile(r"\b(نیمه مبله|مبله|مبلمان|furnished|möbliert|moebliert)\b")),
    ("bills", re.compile(r"\b(با قبوض|قبوض داخل|قبوض|بیل|با بیل|utilities included|bills included|utilities|bills|all inclusive|inclusive)\b")),
    ("warm", re.compile(r"\b(اجاره گرم|warmmiete|warm)\b")),
    ("reg", re.compile(r"\b(با ملد|ملد|آنملدونگ|anmeldung|anmeldbar)\b")),
    ("owner", re.compile(r"\b(از مالک|فقط مالک|مالک|بدون بنگاه|بدون مشاور|بدون ایجنت|no agent|no agents|from owner|by owner|owner|provisionsfrei|privat)\b")),
)
_TRANSIT = re.compile(r"\b(?:(?:نزدیک|کنار|دم|near|close to|nahe|nah)\s+)?(?P<word>مترو|ایستگاه مترو|tube|subway|metro|ttc|skytrain|اسکای ترین|u bahn|u-bahn|s bahn|s-bahn|ubahn|sbahn)\b")
_TRANSIT_Q = {"subway": "مترو", "metro": "مترو", "tube": "مترو", "ttc": "مترو", "ایستگاه مترو": "مترو", "مترو": "مترو", "اسکای ترین": "skytrain", "u bahn": "u-bahn", "ubahn": "u-bahn", "s bahn": "s-bahn", "sbahn": "s-bahn"}

_PLACE_WORDS = {"نزدیک", "نزدیکی", "حوالی", "اطراف", "سمت", "محله", "منطقه", "near", "nearby", "around", "in", "at", "در", "توی", "تو", "nähe", "bei", "am", "im"}
_STOP = {
    "و", "یا", "با", "به", "از", "تا", "که", "را", "رو", "برای", "واسه", "یک", "یه", "هم", "هست", "باشه", "باشد", "باشد.", "داشته", "نزدیک", "ای", "ی", "the", "a", "an", "for", "with", "and", "or", "of", "to", "in", "at", "on", "near", "i", "me", "my", "ein", "eine", "einen", "mit", "für", "und", "oder", "im", "am", "ich", "suche",
    "دنبال", "میگردم", "می", "گردم", "خوام", "میخوام", "میخواهم", "خواهم", "لازم", "دارم", "نیاز", "احتیاج", "looking", "need", "want", "search", "searching", "find", "please", "لطفا", "لطفاً", "پیدا", "کن", "کنید",
    "خانه", "خونه", "منزل", "آگهی", "اگهی", "جا", "مکان", "ملک", "اجاره", "اجاره‌ای", "اجاره ای", "کرایه", "rent", "rental", "home", "place", "housing", "miete", "wohnen",
    "بودجه", "budget", "قیمت", "price", "ماهانه", "ماهی", "ماه", "در", "month", "monthly", "per", "pro", "monat", "دلار", "یورو", "تومن", "cad", "eur", "euro", "فقط", "only", "مناسب", "ارزان", "ارزون", "cheap", "خوب", "good", "nice",
}
_NOISE = {"سلام", "درود", "خوبی", "چطوری", "چطورید", "خوبید", "مرسی", "ممنون", "تشکر", "حالت", "چه", "خبر", "hello", "hi", "hey", "thanks", "thank", "you", "hallo", "danke", "test", "تست", "asdf"}


@dataclass
class RuleResult:
    spec: SearchSpec
    labels: list[dict] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    leftover: list[str] = field(default_factory=list)
    unknown_area: dict | None = None
    other_city: dict | None = None


def _date_from(month: int, day: int, today: date) -> date | None:
    """The next time this day of the year comes. An impossible date means nothing."""
    for year in (today.year, today.year + 1):
        try:
            found = date(year, month, min(day, 28) if month == 2 else day)
        except ValueError:
            return None
        if found >= today:
            return found
    return None


def _rules_date(text: str, spec: SearchSpec, today: date) -> str:
    def replace(match: re.Match) -> str:
        month_word = match["month"] or match["month2"]
        raw_day = match["day"] or match["day2"]
        if not raw_day and month_word in _NEEDS_DAY:
            return match[0]
        day = _DAY_WORDS.get(raw_day, 0) if raw_day and not raw_day.isdigit() else int(raw_day or 1)
        found = _date_from(_MONTH_OF[month_word], day, today)
        if found is None:
            return match[0]
        if match["prefix"] in {"تا", "تا قبل از", "قبل از", "by", "before", "bis"}:
            spec.by = found.isoformat()
        else:
            spec.move = found.isoformat()
        return " | "

    text = _DATE.sub(replace, text, count=1)
    if not spec.by and _SOON.search(text):
        spec.by = (today + timedelta(days=SOON_DAYS)).isoformat()
        text = _SOON.sub(" | ", text)
    return text


def _word_number(raw: str) -> float:
    return float(_NUM_WORDS.get(raw, raw))


def _rules_rooms(text: str, spec: SearchSpec, metro: Metro, ignored: list[str]) -> str:
    def add(rooms: int, said: str) -> None:
        # A Quebec «۴½» has no bedroom count, so a bedroom tile there finds nothing (P1-07).
        if metro.is_quebec():
            if "apartment" not in spec.types:
                spec.types.append("apartment")
            ignored.append(said)
            return
        token = f"bed{min(rooms, 3)}"
        if token not in spec.types:
            spec.types.append(token)

    def beds(match: re.Match) -> str:
        add(int(_word_number(match["n"])), match[0].strip())
        return " | "

    text = _PLUS_DEN.sub(beds, text)
    text = _BEDS.sub(beds, text)

    def quebec(match: re.Match) -> str:
        if "apartment" not in spec.types:
            spec.types.append("apartment")
        return " | "

    return _QUEBEC_SIZE.sub(quebec, text)


def _rules_size(text: str, spec: SearchSpec) -> str:
    def metres(value: float) -> None:
        spec.size_min, spec.size_max = round(value * 0.875), round(value * 1.125)

    def replace(match: re.Match) -> str:
        metres(int(match["n"]))
        return " | "

    def replace_feet(match: re.Match) -> str:
        metres(int(match["n"]) / 10.764)
        return " | "

    text = _SIZE.sub(replace, text, count=1)
    return _SQFT.sub(replace_feet, text, count=1)


def _rules_budget(text: str, spec: SearchSpec) -> str:
    def thousands(match: re.Match) -> str:
        rest = match["rest"]
        extra = 500 if rest == "پانصد" else int(rest or 0)
        return f" {int(_word_number(match['n']) * 1000) + extra} "

    text = _THOUSANDS.sub(thousands, text)

    def fits(value: str) -> int | None:
        number = int(value)
        return number if PRICE_LOW <= number <= PRICE_HIGH else None

    def both(match: re.Match) -> str:
        low, high = fits(match[1]), fits(match[2])
        if not low or not high:
            return match[0]
        spec.min, spec.max = min(low, high), max(low, high)
        return " | "

    def top(match: re.Match) -> str:
        high = fits(match[1])
        if not high or spec.max:
            return match[0]
        spec.max = high
        return " | "

    def bottom(match: re.Match) -> str:
        low = fits(match[1])
        if not low or spec.min:
            return match[0]
        spec.min = low
        return " | "

    def about(match: re.Match) -> str:
        value = fits(match[1])
        if not value or value < BARE_PRICE_LOW or spec.min or spec.max:
            return match[0]
        spec.min, spec.max = round(value * 0.9 / 50) * 50, round(value * 1.1 / 50) * 50
        return " | "

    text = _BETWEEN.sub(both, text, count=1)
    text = _RANGE.sub(both, text, count=1)
    text = _MAX.sub(top, text, count=1)
    text = _MAX_AFTER.sub(top, text, count=1)
    text = _MIN.sub(bottom, text, count=1)
    return _ABOUT.sub(about, text, count=1)


def _rules_words(text: str, spec: SearchSpec, metro: Metro, ignored: list[str]) -> str:
    def said_only(match: re.Match) -> str:
        ignored.append(match[0].strip())
        return " | "

    text = _NO_FILTER.sub(said_only, text)
    if _WANTED.search(text):
        spec.kind = "wanted"
        text = _WANTED.sub(" | ", text)
    for name, pattern in _TOGGLES:
        if not pattern.search(text):
            continue
        if toggle_allowed(name, metro.country):
            setattr(spec, name, True)
        else:
            ignored.extend(found.group(0).strip() for found in pattern.finditer(text))
        text = pattern.sub(" | ", text)
    for name, pattern in _GENDERS:
        if pattern.search(text):
            spec.gender = spec.gender or name
            text = pattern.sub(" | ", text)
    for name, pattern in _TERMS:
        if pattern.search(text):
            spec.term = spec.term or name
            text = pattern.sub(" | ", text)
    for name, pattern in _TYPES:
        if pattern.search(text):
            if name not in spec.types:
                spec.types.append(name)
            text = pattern.sub(" | ", text)
    found = _TRANSIT.search(text)
    if found:
        word = found["word"]
        spec.q = _TRANSIT_Q.get(word, word)
        text = _TRANSIT.sub(" | ", text)
    return text


def _is_stop(word: str) -> bool:
    return word in _STOP or word in _NOISE or word in _PLACE_WORDS or not any(ch.isalpha() for ch in word)


def _rules_places(text: str, spec: SearchSpec, metro: Metro, out: RuleResult) -> None:
    """Whole names only, longest first. What follows «نزدیک» and names nothing is an unknown place, not a job for the model."""
    for segment in text.split("|"):
        words = segment.split()
        taken = [False] * len(words)
        for size in range(min(4, len(words)), 0, -1):
            for start in range(len(words) - size + 1):
                if any(taken[start : start + size]):
                    continue
                piece = words[start : start + size]
                if _is_stop(piece[0]) or _is_stop(piece[-1]):
                    continue
                phrase = " ".join(piece)
                hit = metro.area_here(phrase) if metro.here else None
                city = None if hit else metro.city_named(phrase) or metro.city_of_area_elsewhere(phrase)
                if hit:
                    area, label = hit
                    if area.pk not in spec.area_ids and len(spec.area_ids) < MAX_NAMES:
                        spec.area_ids.append(area.pk)
                        out.labels.append({"id": area.pk, "label": label, "name": area.fa})
                elif city:
                    # Its own city adds nothing. Another one is asked about, not silently applied (F08#13).
                    if not metro.here or _metro_key(city.city) != _metro_key(metro.here.city):
                        out.other_city = out.other_city or other_city_row(city)
                else:
                    continue
                for index in range(start, start + size):
                    taken[index] = True

        run: list[str] = []
        after_place = False

        def close() -> None:
            nonlocal run, after_place
            if run:
                phrase = " ".join(run)
                if after_place and metro.here and not out.unknown_area:
                    out.unknown_area = {"text": _titled(phrase), "suggestions": metro.suggestions(phrase)}
                else:
                    out.leftover.append(phrase)
            run = []
            after_place = False

        for index, word in enumerate(words):
            if taken[index]:
                close()
            elif word in _PLACE_WORDS:
                close()
                after_place = True
            elif _is_stop(word):
                if run:
                    close()
            else:
                run.append(word)
        close()


def extract(normalized: str, metro: Metro, today: date) -> RuleResult:
    spec = SearchSpec()
    ignored: list[str] = []
    text = _rules_date(normalized, spec, today)
    text = _rules_rooms(text, spec, metro, ignored)
    text = _rules_size(text, spec)
    text = _rules_budget(text, spec)
    text = _rules_words(text, spec, metro, ignored)
    out = RuleResult(spec=spec, ignored=ignored)
    _rules_places(text, spec, metro, out)
    # A bedroom tile already means an apartment. Both together would widen it to every apartment.
    if "apartment" in spec.types and any(token.startswith("bed") for token in spec.types):
        spec.types.remove("apartment")
    out.ignored = list(dict.fromkeys(out.ignored))[:MAX_NAMES]
    return out
