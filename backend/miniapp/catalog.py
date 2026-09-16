from __future__ import annotations

from datetime import date

from item_requests.locations import locations_payload
from item_requests.models import Category, ItemRequest, RequestType
from matching.contact import CATEGORY_EMOJI, country_flag

CARD_ROUTE_CITY_LIMIT = 3


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
    code = (country_code or "").upper()
    needle = (slug or "").casefold()
    for country in locations:
        if country["code"] != code:
            continue
        for city in country["cities"]:
            if city["slug"].casefold() == needle:
                return localized_name(city, locale)
    return slug


def place_flag(locations: list[dict], country_code: str) -> str:
    code = (country_code or "").upper()
    for country in locations:
        if country["code"] == code:
            return country.get("flag") or country_flag(code)
    return country_flag(code)


def place_label(locations: list[dict], country_code: str, slug: str, locale: str) -> str:
    flag = place_flag(locations, country_code)
    name = city_label(locations, country_code, slug, locale)
    return f"{flag} {name}".strip() if flag else name


def grouped_place_label(
    locations: list[dict],
    stops: list[tuple[str, str]],
    locale: str,
    *,
    max_cities: int | None = None,
) -> str:
    pairs = [(country, city) for country, city in stops if country and city]
    if not pairs:
        return ""
    remaining = 0
    shown = pairs
    if max_cities is not None and len(pairs) > max_cities:
        shown = pairs[:max_cities]
        remaining = len(pairs) - max_cities
    groups: list[tuple[str, list[str]]] = []
    for country, slug in shown:
        name = city_label(locations, country, slug, locale)
        if groups and groups[-1][0] == country:
            groups[-1][1].append(name)
        else:
            groups.append((country, [name]))
    glue = " و " if locale == "fa" else ", "
    parts = []
    for country, names in groups:
        flag = place_flag(locations, country)
        cities = glue.join(names)
        parts.append(f"{flag} {cities}".strip() if flag else cities)
    text = glue.join(parts)
    if remaining:
        text = f"{text} +{remaining}"
    return text


def route_arrow(locale: str = "") -> str:
    return "←" if locale == "fa" else "→"


def join_destinations(labels: list[str], locale: str) -> str:
    names = [name for name in labels if name]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    glue = " و " if locale == "fa" else ", "
    return glue.join(names)


def format_route_text(origin: str, destinations: list[str], locale: str) -> str:
    dest = join_destinations(destinations, locale)
    if not dest:
        return origin
    return f"{origin} {route_arrow(locale)} {dest}"


def ltr_embed(text: str) -> str:
    return f"\u202A{text}\u202C"


def route_label(
    locations: list[dict],
    origin_country: str,
    origin_city: str,
    destination_country: str,
    destination_city: str,
    locale: str,
    destination_stops: list[tuple[str, str]] | None = None,
    *,
    compact: bool = False,
) -> str:
    dests = destination_stops or [(destination_country, destination_city)]
    origin = grouped_place_label(locations, [(origin_country, origin_city)], locale)
    dest = grouped_place_label(
        locations,
        dests,
        locale,
        max_cities=CARD_ROUTE_CITY_LIMIT if compact else None,
    )
    return format_route_text(origin, [dest] if dest else [], locale)


def item_route_label(
    locations: list[dict],
    item: ItemRequest,
    locale: str,
    *,
    compact: bool = False,
) -> str:
    return route_label(
        locations,
        item.origin_country,
        item.origin_city,
        item.destination_country,
        item.destination_city,
        locale,
        destination_stops=item.destination_stop_pairs(),
        compact=compact,
    )


def category_label(categories: list[dict], code: str, locale: str) -> str:
    for category in categories:
        if category["code"] == code:
            return localized_name(category, locale)
    return code


def format_category_line(categories: list[dict], code: str, locale: str) -> str:
    emoji = format_category_emoji(code)
    return f"{emoji} {category_label(categories, code, locale)}"


def format_category_emoji(code: str) -> str:
    return CATEGORY_EMOJI.get(code, "📦")


def format_kg(value) -> str:
    if value is None:
        return "—"
    amount = float(value)
    if amount.is_integer():
        return str(int(amount))
    return f"{amount:.2f}".rstrip("0").rstrip(".")


def format_baggage_kg(value, unit: str = "KG", emoji: str = "🧳") -> str:
    kg = format_kg(value)
    if kg == "—":
        return kg
    return f"{emoji} {kg} {unit}"


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
