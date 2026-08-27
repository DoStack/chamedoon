from __future__ import annotations

from datetime import date, timedelta

from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

SKIP = "Skip"
CANCEL = "Cancel"
DONE = "Done"
SUBMIT = "Submit"
TODAY = "Today"


class CreateRequest(StatesGroup):
    origin_country = State()
    origin_city = State()
    dest_country = State()
    dest_city = State()
    date_from = State()
    date_to = State()
    desired_date = State()
    flight_date = State()
    amount = State()
    categories = State()
    exclusions = State()
    description = State()
    confirm = State()


def parse_date(text: str) -> str:
    value = text.strip()
    lowered = value.lower()
    if lowered == TODAY.lower() or lowered == "today":
        return date.today().isoformat()
    if lowered == "tomorrow":
        return (date.today() + timedelta(days=1)).isoformat()
    return date.fromisoformat(value).isoformat()


def country_by_label(countries: list[dict], label: str) -> dict | None:
    for country in countries:
        name = country.get("name_en") or ""
        if label == name or label == country.get("code"):
            return country
    return None


def city_by_label(cities: list[dict], label: str) -> dict | None:
    for city in cities:
        name = city.get("name_en") or ""
        if label == name or label == city.get("slug"):
            return city
    return None


def category_keyboard(categories: list[dict], selected: list[str], *, allow_skip: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for category in categories:
        code = category["code"]
        name = category.get("name_en") or code
        mark = "✅ " if code in selected else ""
        rows.append([InlineKeyboardButton(text=f"{mark}{name}", callback_data=f"cat:{code}")])
    rows.append([InlineKeyboardButton(text=DONE, callback_data="cat:done")])
    if allow_skip:
        rows.append([InlineKeyboardButton(text=SKIP, callback_data="cat:skip")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
