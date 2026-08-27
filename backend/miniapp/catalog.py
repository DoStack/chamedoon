from __future__ import annotations

from datetime import date

from item_requests.models import Category, Country
from matching.contact import country_flag


def locations_payload() -> list[dict]:
    countries = Country.objects.filter(is_active=True).prefetch_related("cities")
    payload = []
    for country in countries:
        cities = [
            {"slug": city.slug, "name_en": city.name_en, "name_fa": city.name_fa}
            for city in country.cities.all()
            if city.is_active
        ]
        payload.append(
            {
                "code": country.code,
                "flag": country_flag(country.code),
                "name_en": country.name_en,
                "name_fa": country.name_fa,
                "cities": cities,
            }
        )
    return payload


def categories_payload() -> list[dict]:
    return list(
        Category.objects.filter(is_active=True).values("code", "name_en", "name_fa", "sort_order")
    )


def localized_name(item: dict | object, locale: str) -> str:
    if isinstance(item, dict):
        return item["name_fa"] if locale == "fa" else item["name_en"]
    return item.name_fa if locale == "fa" else item.name_en


def city_label(locations: list[dict], country_code: str, slug: str, locale: str) -> str:
    for country in locations:
        if country["code"] != country_code:
            continue
        for city in country["cities"]:
            if city["slug"] == slug:
                return localized_name(city, locale)
    return slug


def route_label(
    locations: list[dict],
    origin_country: str,
    origin_city: str,
    destination_country: str,
    destination_city: str,
    locale: str,
) -> str:
    return (
        f"{city_label(locations, origin_country, origin_city, locale)}"
        f" → {city_label(locations, destination_country, destination_city, locale)}"
    )


def category_label(categories: list[dict], code: str, locale: str) -> str:
    for category in categories:
        if category["code"] == code:
            return localized_name(category, locale)
    return code


def format_kg(value) -> str:
    if value is None:
        return "—"
    amount = float(value)
    if amount.is_integer():
        return str(int(amount))
    return f"{amount:.2f}".rstrip("0").rstrip(".")


def format_date_range(start: date, end: date) -> str:
    if start == end:
        return start.isoformat()
    return f"{start.isoformat()} – {end.isoformat()}"


def format_score_percent(score) -> str:
    return f"{int(round(float(score)))}%"
