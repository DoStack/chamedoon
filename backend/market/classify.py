from __future__ import annotations

import re

from market.catalog import is_country_place
from market.models import MarketRole
from market.places import find_parenthetical_cities, find_places

_NOISE = (
    "لیست خرید",
    "taskmanager",
    "machino24",
    "flightiranbot",
    "تاکسی در رم",
    "چارتر",
    "offer property",
    "pinned a photo",
    "به هیچ عنوان کالای",
    "ترانسفر و ترانسپورت",
    "قوانین حمل وسایل در کابین",
    "ادمین تبلیغات",
    "با هدف ارائه",
    "لینک کانال",
    "لینک گروه",
    "گروه خرید",
    "رزرو بلیط",
    "joinchat",
    "t.me/+",
    "قوانین و مقررات گمرکی",
)


def classify_role(text: str) -> str:
    lowered = text.lower()
    if any(marker in text or marker in lowered for marker in _NOISE):
        return MarketRole.NOISE
    if re.search(r"قیمت:\s*[\d,]+\s*تومان", text) and text.count("مقصد") > 3:
        return MarketRole.NOISE
    if any(
        marker in text
        for marker in (
            "مسافر نیستم",
            "#خرید_بار",
            "خریدار بار",
            "کسی هست",
            "برام ببره",
            "مسافر میخوام",
            "نیاز به مسافر",
        )
    ):
        return MarketRole.DEMAND
    if any(
        tag in text
        for tag in (
            "#فروش_بار",
            "#مسافر",
            "#قبول_بار",
            "#حمل_بار",
            "فروش بار",
            "پذیرفته می",
            "پذیرفته می‌",
        )
    ):
        return MarketRole.SUPPLY
    supply_hits = sum(
        marker in text
        for marker in (
            "مسافر هستم",
            "مسافرم",
            "قبول بار",
            "قبول مدارک",
            "پذیرش بار",
            "حمل بار",
            "بار قابل",
            "قابل رویت",
            "قابل رويت",
            "پرواز",
        )
    )
    demand_hits = sum(
        marker in text
        for marker in (
            "#بار",
            "بار دارم",
            "#ارسال_بار",
            "ارسال بار",
            "ارسال مدارک",
            "ارسال مدرک",
            "آوردن بار",
            "ببره",
        )
    )
    if supply_hits > demand_hits and supply_hits:
        return MarketRole.SUPPLY
    if demand_hits > supply_hits and demand_hits:
        return MarketRole.DEMAND
    if "مسافر" in text:
        return MarketRole.SUPPLY
    return MarketRole.UNKNOWN


def is_courier_request(text: str, stored_role: str = "") -> bool:
    guessed = classify_role(text)
    if guessed == MarketRole.NOISE:
        return False
    if guessed in {MarketRole.SUPPLY, MarketRole.DEMAND}:
        return True
    return stored_role in {MarketRole.SUPPLY, MarketRole.DEMAND}


def extract_weight_kg(text: str) -> float | None:
    match = re.search(r"(\d+(?:[./]\d+)?)\s*(?:کیلوگرم|کیلو|kg)", text, re.I)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ".").replace("/", "."))
    except ValueError:
        return None


def extract_stops(text: str) -> tuple[dict[str, str] | None, list[dict[str, str]]]:
    places = find_places(text)
    origin = None
    dests: list[dict[str, str]] = []
    origin_line = re.search(r"مبدا[:\s.]*([^\n]{0,50})", text)
    dest_line = re.search(r"مقصد[:\s.]*([^\n]{0,80})", text)
    if origin_line:
        origin_chunk = re.split(r"مقصد", origin_line.group(1), maxsplit=1)[0]
        found = find_places(origin_chunk)
        if found:
            origin = _prefer_origin(found)
    if dest_line:
        dests = _unique_places(find_places(dest_line.group(1)))
    if not origin or not dests:
        arrow = re.search(r"([\s\S]{0,80}?)(?:به|→|➜|->)([\s\S]{0,160})", text)
        if arrow:
            left, right = find_places(arrow.group(1)), find_places(arrow.group(2))
            if left and not origin:
                origin = _prefer_origin(left)
            if right and not dests:
                dests = _unique_places(right, skip=origin)
    if not origin and not dests and len(places) >= 2:
        origin = _prefer_origin(places[:1]) or places[0]
        dests = _unique_places(places[1:], skip=origin)
    elif origin and not dests:
        dests = _unique_places(places, skip=origin)
    elif dests and not origin:
        others = [item for item in places if item not in dests]
        if others:
            origin = _prefer_origin(others)
    dests = _foreign_dests(origin, _unique_places(dests, skip=origin))
    origin, dests = _apply_parentheticals(text, origin, dests)
    dests = _foreign_dests(origin, _unique_places(dests, skip=origin))
    return origin, dests


def _apply_parentheticals(
    text: str,
    origin: dict[str, str] | None,
    dests: list[dict[str, str]],
) -> tuple[dict[str, str] | None, list[dict[str, str]]]:
    extras = find_parenthetical_cities(text)
    if not extras:
        return origin, dests
    if origin and is_country_place(origin):
        same = [place for place in extras if place.get("country") == origin.get("country")]
        if same:
            origin = _prefer_origin(same) or origin
    city_extras = [place for place in extras if not is_country_place(place)]
    if city_extras:
        extra_countries = {place.get("country") for place in city_extras}
        dests = [place for place in dests if not (is_country_place(place) and place.get("country") in extra_countries)]
        dests = _unique_places(dests + city_extras, skip=origin)
    return origin, dests


def extract_route(text: str) -> tuple[dict[str, str] | None, dict[str, str] | None]:
    origin, dests = extract_stops(text)
    return origin, dests[-1] if dests else None


def _prefer_origin(places: list[dict[str, str]]) -> dict[str, str] | None:
    if not places:
        return None
    cities = [place for place in places if not is_country_place(place)]
    return cities[0] if cities else places[0]


def _foreign_dests(origin: dict[str, str] | None, dests: list[dict[str, str]]) -> list[dict[str, str]]:
    if not origin or not dests:
        return dests
    origin_country = origin.get("country") or ""
    foreign = [place for place in dests if place.get("country") != origin_country]
    return foreign or dests


def _unique_places(
    places: list[dict[str, str]],
    *,
    skip: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    seen: list[dict[str, str]] = []
    for place in places:
        if skip and place == skip:
            continue
        if place not in seen:
            seen.append(place)
    return seen
