from __future__ import annotations

from django.db.models import Q
from django.utils.text import slugify

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


def catalog_location(place: dict[str, str] | None, *, create_missing: bool = True) -> tuple[str, str] | None:
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
    return _lookup_or_create_city(country, name, create_missing=create_missing)


def is_country_place(place: dict[str, str] | None) -> bool:
    return bool(place and place.get("city") in COUNTRY_ONLY)


def catalog_destinations(place: dict[str, str] | None, *, create_missing: bool = True) -> list[tuple[str, str]]:
    if not place:
        return []
    if is_country_place(place):
        return cities_for_country(place.get("country") or "")
    loc = catalog_location(place, create_missing=create_missing)
    return [loc] if loc else []


def resolve_destination_locs(
    dests: list[dict[str, str]] | None,
    *,
    origin_loc: tuple[str, str] | None = None,
    create_missing: bool = True,
) -> list[tuple[str, str]]:
    dests = dests or []
    city_places = [place for place in dests if not is_country_place(place)]
    country_places = [place for place in dests if is_country_place(place)]
    locs: list[tuple[str, str]] = []
    city_countries: set[str] = set()
    for place in city_places:
        loc = catalog_location(place, create_missing=create_missing)
        if loc and loc != origin_loc and loc not in locs:
            locs.append(loc)
            city_countries.add(loc[0])
    for place in country_places:
        country = (place.get("country") or "").upper()
        if country in city_countries:
            continue
        for loc in cities_for_country(country):
            if loc != origin_loc and loc not in locs:
                locs.append(loc)
    return locs


def _lookup_or_create_city(country: str, name: str, *, create_missing: bool) -> tuple[str, str] | None:
    raw = (name or "").strip()
    code = (country or "").upper().strip()
    if not raw or not code or raw in COUNTRY_ONLY:
        return None
    from item_requests.models import City

    slug = slugify(raw, allow_unicode=True)[:64]
    existing = (
        City.objects.filter(country__code=code, is_active=True)
        .filter(Q(slug__iexact=slug) | Q(name_en__iexact=raw) | Q(name_fa__iexact=raw))
        .first()
    )
    if existing:
        return existing.country.code, existing.slug
    if not create_missing:
        return None
    from item_requests.locations import resolve_or_create_city

    city = resolve_or_create_city(code, raw)
    if city is None:
        return None
    return city.country.code, city.slug


def cities_for_country(country: str) -> list[tuple[str, str]]:
    code = (country or "").upper()
    default = COUNTRY_DEFAULT_SLUG.get(code)
    cities = [(item["country"], item["slug"]) for item in CITIES if item["country"] == code]
    if default:
        cities = [item for item in cities if item[1] != default] + [(code, default)]
    return cities
