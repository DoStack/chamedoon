from __future__ import annotations

from item_requests.seed import CITIES

COUNTRY_ONLY = {
    "Iran",
    "Canada",
    "United States",
    "United Kingdom",
    "Germany",
    "Turkey",
    "UAE",
    "Armenia",
    "Iraq",
    "France",
    "Netherlands",
    "Austria",
    "Sweden",
    "Norway",
    "Denmark",
    "Switzerland",
    "Italy",
}

COUNTRY_DEFAULT_SLUG = {
    "IR": "tehran",
    "CA": "toronto",
    "US": "los-angeles",
    "GB": "london",
    "DE": "berlin",
    "TR": "istanbul",
    "AE": "dubai",
    "AM": "yerevan",
    "IQ": "baghdad",
    "FR": "paris",
    "NL": "amsterdam",
    "AT": "vienna",
    "SE": "stockholm",
    "NO": "oslo",
    "DK": "copenhagen",
    "CH": "zurich",
    "IT": "milan",
}

CITY_REMAP = {
    ("Karaj", "IR"): ("tehran", "IR"),
    ("Sari", "IR"): ("tehran", "IR"),
    ("Rasht", "IR"): ("tehran", "IR"),
    ("Yazd", "IR"): ("tehran", "IR"),
    ("Kish", "IR"): ("tehran", "IR"),
    ("Ahvaz", "IR"): ("tehran", "IR"),
    ("Urmia", "IR"): ("tabriz", "IR"),
}

_SLUGS = {(item["name_en"], item["country"]): item["slug"] for item in CITIES}


def catalog_location(place: dict[str, str] | None) -> tuple[str, str] | None:
    if not place:
        return None
    name, country = place.get("city") or "", place.get("country") or ""
    if not country:
        return None
    remapped = CITY_REMAP.get((name, country))
    if remapped:
        return remapped[1], remapped[0]
    slug = _SLUGS.get((name, country))
    if slug:
        return country, slug
    if name in COUNTRY_ONLY:
        default = COUNTRY_DEFAULT_SLUG.get(country)
        if default:
            return country, default
    return None
