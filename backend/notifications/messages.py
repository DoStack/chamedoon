from __future__ import annotations

from datetime import date

from item_requests.models import City, ItemRequest
from matching.contact import intro_draft_for_match
from matching.models import Match
from support.models import SupportTicket, TicketSubject


def format_route(item_request: ItemRequest) -> str:
    origin = city_label(item_request.origin_country, item_request.origin_city)
    destinations = [city_label(country, city) for country, city in item_request.destination_stop_pairs()]
    dest = " → ".join(destinations) if destinations else city_label(
        item_request.destination_country, item_request.destination_city
    )
    return f"{origin} → {dest}"


def format_travel_dates(item_request: ItemRequest) -> str:
    if item_request.type == "DEMAND":
        desired = item_request.desired_date or item_request.date_from
        return format_date_range(desired, desired)
    window = format_date_range(item_request.date_from, item_request.date_to)
    if item_request.flight_date:
        flight = format_date_range(item_request.flight_date, item_request.flight_date)
        return f"{flight} · {window}"
    return window


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
    desired = match.demand_request.desired_date or match.demand_request.date_from
    travel = format_travel_dates(match.supply_request)
    requester = match.requester_user()
    name = (requester.first_name if requester else "") or "Someone"
    return (
        "You have a new match.\n\n"
        f"{name} is a match.\n"
        f"{route}\n"
        f"{format_date_range(desired, desired)}\n"
        f"{travel}\n\n"
        "Message them on Telegram to arrange the handover."
    )


def match_accepted_text() -> str:
    return "Your request has been accepted."


def match_rejected_text() -> str:
    return "Your match request was rejected."


def match_cancelled_text() -> str:
    return "A pending match request was cancelled."


def close_listing_prompt_text() -> str:
    return "Do you want to close your listing?"


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
        lines.append("They have no public @username. Open Chamedoon to message them.")
    draft = intro_draft_for_match(match, recipient)
    if draft:
        lines.extend(["", draft])
    return "\n".join(lines)


def support_reply_text(ticket: SupportTicket) -> str:
    subject = ticket.get_subject_display() if ticket.subject in TicketSubject.values else ticket.subject
    return (
        "🔔 Chamedoon Support\n\n"
        "You have a new reply to your support ticket.\n\n"
        f"Subject:\n{subject}"
    )


def support_auto_closed_text() -> str:
    return "Your Chamedoon support ticket has been closed because we did not receive a reply for 3 days."
