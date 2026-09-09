from __future__ import annotations

import re

from market.models import MarketRole
from market.places import find_places

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
    if any(tag in text for tag in ("#فروش_بار", "#مسافر", "#قبول_بار", "#حمل_بار", "فروش بار")):
        return MarketRole.SUPPLY
    supply_hits = sum(
        marker in text
        for marker in ("مسافر هستم", "قبول بار", "قبول مدارک", "پذیرش بار", "حمل بار")
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


def extract_weight_kg(text: str) -> float | None:
    match = re.search(r"(\d+(?:[./]\d+)?)\s*(?:کیلوگرم|کیلو|kg)", text, re.I)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ".").replace("/", "."))
    except ValueError:
        return None


def extract_route(text: str) -> tuple[dict[str, str] | None, dict[str, str] | None]:
    places = find_places(text)
    origin = dest = None
    origin_line = re.search(r"مبدا[:\s.]*([^\n]{0,50})", text)
    dest_line = re.search(r"مقصد[:\s.]*([^\n]{0,50})", text)
    if origin_line:
        found = find_places(origin_line.group(1))
        if found:
            origin = found[0]
    if dest_line:
        found = find_places(dest_line.group(1))
        if found:
            dest = found[0]
    if not origin or not dest:
        arrow = re.search(r"(.{0,30})(?:به|→|➜|->)(.{0,30})", text)
        if arrow:
            left, right = find_places(arrow.group(1)), find_places(arrow.group(2))
            if left and not origin:
                origin = left[0]
            if right and not dest:
                dest = right[0]
    if not origin and not dest and len(places) >= 2:
        origin, dest = places[0], places[1]
    elif origin and not dest:
        others = [item for item in places if item != origin]
        if others:
            dest = others[0]
    elif dest and not origin:
        others = [item for item in places if item != dest]
        if others:
            origin = others[0]
    return origin, dest
