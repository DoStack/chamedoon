from __future__ import annotations

from datetime import date

from item_requests.models import Category, Country, ItemRequest, RequestType
from matching.contact import CATEGORY_EMOJI, country_flag


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
    rows = list(
        Category.objects.filter(is_active=True).values("code", "name_en", "name_fa", "sort_order")
    )
    for row in rows:
        row["emoji"] = CATEGORY_EMOJI.get(row["code"], "📦")
    return rows


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


def place_label(locations: list[dict], country_code: str, slug: str, locale: str) -> str:
    flag = ""
    for country in locations:
        if country["code"] == country_code:
            flag = country.get("flag") or ""
            break
    if not flag:
        flag = country_flag(country_code)
    name = city_label(locations, country_code, slug, locale)
    return f"{flag} {name}".strip() if flag else name


def route_label(
    locations: list[dict],
    origin_country: str,
    origin_city: str,
    destination_country: str,
    destination_city: str,
    locale: str,
) -> str:
    return (
        f"{place_label(locations, origin_country, origin_city, locale)}"
        f" → {place_label(locations, destination_country, destination_city, locale)}"
    )


def category_label(categories: list[dict], code: str, locale: str) -> str:
    for category in categories:
        if category["code"] == code:
            return localized_name(category, locale)
    return code


def format_category_line(categories: list[dict], code: str, locale: str) -> str:
    emoji = CATEGORY_EMOJI.get(code, "📦")
    return f"{emoji} {category_label(categories, code, locale)}"


def format_kg(value) -> str:
    if value is None:
        return "—"
    amount = float(value)
    if amount.is_integer():
        return str(int(amount))
    return f"{amount:.2f}".rstrip("0").rstrip(".")


def format_baggage_kg(value, unit: str = "KG") -> str:
    kg = format_kg(value)
    if kg == "—":
        return kg
    return f"🧳 {kg} {unit}"


def format_date_range(start: date, end: date) -> str:
    if start == end:
        return start.isoformat()
    return f"{start.isoformat()} – {end.isoformat()}"


def format_day(value: date | None) -> str:
    if value is None:
        return "—"
    return value.isoformat()


def format_flight_line(value: date | None) -> str:
    if value is None:
        return ""
    return f"✈️ {value.isoformat()}"


def format_desired_line(value: date | None) -> str:
    if value is None:
        return ""
    return f"📅 {value.isoformat()}"


_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MONTHS_FA = (
    "ژانویه",
    "فوریه",
    "مارس",
    "آوریل",
    "مه",
    "ژوئن",
    "ژوئیه",
    "اوت",
    "سپتامبر",
    "اکتبر",
    "نوامبر",
    "دسامبر",
)


def format_month_day(value: date | None, locale: str = "en") -> str:
    if value is None:
        return "—"
    months = _MONTHS_FA if locale == "fa" else _MONTHS_EN
    return f"{months[value.month - 1]} {value.day}"


def format_item_dates(item: ItemRequest) -> str:
    if item.type == RequestType.DEMAND:
        return format_day(item.desired_date or item.date_from)
    window = format_date_range(item.date_from, item.date_to)
    if item.flight_date:
        return f"{format_day(item.flight_date)} · {window}"
    return window
