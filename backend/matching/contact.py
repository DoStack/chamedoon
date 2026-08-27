from __future__ import annotations

from urllib.parse import quote

from item_requests.models import City
from matching.models import Match, MatchStatus
from users.models import User

DEMANDER_DRAFT = (
    "سلام {name}، از کولبار وصل شدیم.\n"
    "من فرستنده‌ام و می‌خوام بسته‌ام را در مسیر {route} بفرستم ({dates}).\n"
    "برای هماهنگی زمان و محل تحویل همین‌جا پیام بده."
)

TRAVELER_DRAFT = (
    "سلام {name}، از کولبار وصل شدیم.\n"
    "من مسافر این مسیرم: {route} ({dates}).\n"
    "ظرفیت حمل دارم؛ برای هماهنگی زمان و محل تحویل همین‌جا پیام بده."
)


def koolbar_intro_draft(*, role: str, name: str, route: str, dates: str) -> str:
    template = DEMANDER_DRAFT if role == "demand" else TRAVELER_DRAFT
    return template.format(
        name=(name or "").strip() or "دوست",
        route=route,
        dates=dates,
    )


def telegram_dm_contact(user: User, draft: str = "") -> dict:
    username = (user.telegram_username or "").strip() or None
    telegram_user_id = int(user.telegram_user_id)
    https_url = f"https://t.me/{username}" if username else None
    if https_url and draft:
        https_url = f"{https_url}?text={quote(draft, safe='')}"
    return {
        "first_name": user.first_name,
        "telegram_username": username,
        "telegram_user_id": telegram_user_id,
        "https_url": https_url,
        "telegram_url": https_url or f"tg://user?id={telegram_user_id}",
        "draft": draft,
    }


def intro_draft_for_match(match: Match, user: User) -> str:
    other_request = match.counterpart_request(user)
    role = match.role_for(user)
    if other_request is None or role is None:
        return ""
    route, dates = _persian_route_and_dates(match)
    return koolbar_intro_draft(
        role=role,
        name=other_request.user.first_name,
        route=route,
        dates=dates,
    )


def contact_for_match(match: Match, user: User) -> dict | None:
    if match.status not in {MatchStatus.CONNECTED, MatchStatus.COMPLETED}:
        return None
    other_request = match.counterpart_request(user)
    if other_request is None:
        return None
    return telegram_dm_contact(other_request.user, intro_draft_for_match(match, user))


def _persian_route_and_dates(match: Match) -> tuple[str, str]:
    demand = match.demand_request
    origin = _city_fa(demand.origin_country, demand.origin_city)
    destination = _city_fa(demand.destination_country, demand.destination_city)
    supply = match.supply_request
    if supply.date_from == supply.date_to:
        dates = supply.date_from.isoformat()
    else:
        dates = f"{supply.date_from.isoformat()} – {supply.date_to.isoformat()}"
    return f"{origin} → {destination}", dates


def _city_fa(country_code: str, slug: str) -> str:
    name = (
        City.objects.filter(country__code=country_code, slug=slug, is_active=True)
        .values_list("name_fa", flat=True)
        .first()
    )
    return name or slug
