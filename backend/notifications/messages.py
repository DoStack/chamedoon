from __future__ import annotations

from datetime import date

from item_requests.models import City, ItemRequest
from matching.models import Match


def format_route(item_request: ItemRequest) -> str:
    origin = city_label(item_request.origin_country, item_request.origin_city)
    destination = city_label(item_request.destination_country, item_request.destination_city)
    return f"{origin} → {destination}"


def format_travel_dates(item_request: ItemRequest) -> str:
    return format_date_range(item_request.date_from, item_request.date_to)


def format_date_range(start: date, end: date) -> str:
    if start == end:
        return f"{start.strftime('%B')} {start.day}"
    if start.month == end.month and start.year == end.year:
        return f"{start.strftime('%B')} {start.day}–{end.day}"
    return f"{start.strftime('%b')} {start.day} – {end.strftime('%b')} {end.day}"


def city_label(country_code: str, slug: str) -> str:
    city = (
        City.objects.filter(country__code=country_code, slug=slug, is_active=True)
        .only("name_en")
        .first()
    )
    if city:
        return city.name_en
    return slug.replace("-", " ").title()


def new_match_text(match: Match) -> str:
    route = format_route(match.demand_request)
    travel = format_travel_dates(match.supply_request)
    return (
        "🤝 You have a potential match!\n\n"
        f"{route}\n"
        f"{travel}\n\n"
        "Open Koolbar to review."
    )


def match_accepted_text() -> str:
    return "✅ Your match has been accepted.\n\nOpen Koolbar to continue."


def connected_text(match: Match, recipient) -> str:
    other_request = match.counterpart_request(recipient)
    other = other_request.user if other_request is not None else None
    lines = [
        "🎉 You are connected!",
        "",
        "Message them on Telegram to arrange the handover.",
    ]
    if other is None:
        return "\n".join(lines)
    lines.extend(
        [
            "",
            f"Name: {other.first_name}",
            f"Telegram ID: {other.telegram_user_id}",
        ]
    )
    username = (other.telegram_username or "").strip()
    if username:
        lines.append(f"Username: @{username}")
        lines.append(f"Message: https://t.me/{username}")
    else:
        lines.append("They have no public @username. Open Koolbar to message them.")
    return "\n".join(lines)
