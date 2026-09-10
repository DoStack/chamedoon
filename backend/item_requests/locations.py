from __future__ import annotations

from django.db.models import Count, Q
from django.utils.text import slugify

from item_requests.models import City, Country, ItemRequest
from matching.contact import country_flag

PINNED_COUNTRY_CODES = ("IR", "CA")


def city_usage_counts() -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    origin = ItemRequest.objects.values("origin_country", "origin_city").annotate(n=Count("id"))
    dest = ItemRequest.objects.values("destination_country", "destination_city").annotate(n=Count("id"))
    for row in origin:
        key = (row["origin_country"], row["origin_city"])
        if key[0] and key[1]:
            counts[key] = counts.get(key, 0) + row["n"]
    for row in dest:
        key = (row["destination_country"], row["destination_city"])
        if key[0] and key[1]:
            counts[key] = counts.get(key, 0) + row["n"]
    return counts


def _local_name(item: Country | City, locale: str) -> str:
    return item.name_fa if locale == "fa" else item.name_en


def locations_payload(*, locale: str = "en") -> list[dict]:
    usage = city_usage_counts()
    pinned = {code: index for index, code in enumerate(PINNED_COUNTRY_CODES)}
    countries = list(Country.objects.filter(is_active=True).prefetch_related("cities"))
    countries.sort(
        key=lambda country: (
            pinned.get(country.code, len(PINNED_COUNTRY_CODES)),
            _local_name(country, locale).casefold(),
        )
    )
    payload = []
    for country in countries:
        cities = [city for city in country.cities.all() if city.is_active]
        cities.sort(
            key=lambda city: (
                -usage.get((country.code, city.slug), 0),
                _local_name(city, locale).casefold(),
            )
        )
        payload.append(
            {
                "code": country.code,
                "flag": country_flag(country.code),
                "name_en": country.name_en,
                "name_fa": country.name_fa,
                "cities": [
                    {"slug": city.slug, "name_en": city.name_en, "name_fa": city.name_fa}
                    for city in cities
                ],
            }
        )
    return payload


def resolve_or_create_city(country_code: str, city_value: str) -> City | None:
    code = (country_code or "").upper().strip()
    raw = (city_value or "").strip()
    if not code or not raw:
        return None
    country = Country.objects.filter(code=code, is_active=True).first()
    if country is None:
        return None
    slug = slugify(raw, allow_unicode=True)[:64]
    if not slug or len(slug) < 2:
        return None
    existing = (
        City.objects.filter(country=country)
        .filter(Q(slug__iexact=slug) | Q(name_en__iexact=raw) | Q(name_fa__iexact=raw))
        .first()
    )
    if existing:
        if not existing.is_active:
            existing.is_active = True
            existing.save(update_fields=["is_active"])
        return existing
    display = raw[:64] if not raw.isascii() else raw.title()[:64]
    city, created = City.objects.get_or_create(
        country=country,
        slug=slug,
        defaults={"name_en": display, "name_fa": display, "is_active": True},
    )
    if not created and not city.is_active:
        city.is_active = True
        city.save(update_fields=["is_active"])
    return city


def destination_stop_pairs(item_request: ItemRequest) -> list[tuple[str, str]]:
    return item_request.destination_stop_pairs()


def destination_key(country: str, city: str) -> str:
    return f"{(country or '').upper().strip()}:{(city or '').strip()}"
