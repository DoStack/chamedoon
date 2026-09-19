from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from api_client import BotApiError, create_request, fetch_categories, fetch_locations
from config import mini_app_https_url
from flow_utils import (
    CANCEL,
    SKIP,
    SUBMIT,
    TODAY,
    CreateRequest,
    category_keyboard,
    city_by_label,
    country_by_label,
    parse_date,
)
from keyboards import (
    cancel_keyboard,
    choices_keyboard,
    confirm_keyboard,
    main_reply_keyboard,
    open_mini_app_inline,
)

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("cancel"))
@router.message(F.text == CANCEL)
async def cancel_flow(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        return
    await state.clear()
    await message.answer("Cancelled.", reply_markup=main_reply_keyboard())


async def start_create_flow(message: Message, state: FSMContext, request_type: str) -> None:
    await state.clear()
    startapp = "demand" if request_type == "DEMAND" else "supply"
    https_url = mini_app_https_url(startapp)
    if https_url:
        label = "📦 Send Package" if request_type == "DEMAND" else "✈️ Can Carry"
        await message.answer(
            "Open Chamedoon to create this request.",
            reply_markup=open_mini_app_inline(startapp, label),
        )
        return
    try:
        categories = fetch_categories()
        locations = fetch_locations()
    except BotApiError:
        logger.exception("Could not load catalog for bot request flow")
        await message.answer("Could not load countries and categories. Try again in a moment.")
        return
    countries = locations.get("countries") or []
    if not countries or not categories:
        await message.answer("Catalog is empty. Seed the backend catalog and try again.")
        return
    await state.set_state(CreateRequest.origin_country)
    await state.update_data(
        type=request_type,
        categories=categories,
        countries=countries,
        selected_categories=[],
        selected_exclusions=[],
    )
    title = "send request" if request_type == "DEMAND" else "traveler request"
    await message.answer(
        f"Let's create your {title}.\nSend /cancel anytime to stop.\n\nOrigin country?",
        reply_markup=choices_keyboard([country["name_en"] for country in countries]),
    )


@router.message(CreateRequest.origin_country)
async def set_origin_country(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    country = country_by_label(data["countries"], message.text or "")
    if country is None:
        await message.answer("Choose a country from the list.")
        return
    await state.update_data(origin_country=country["code"], origin_cities=country.get("cities") or [])
    await state.set_state(CreateRequest.origin_city)
    cities = [city["name_en"] for city in country.get("cities") or []]
    await message.answer("Origin city?", reply_markup=choices_keyboard(cities))


@router.message(CreateRequest.origin_city)
async def set_origin_city(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    city = city_by_label(data.get("origin_cities") or [], message.text or "")
    if city is None:
        await message.answer("Choose a city from the list.")
        return
    await state.update_data(origin_city=city["slug"])
    await state.set_state(CreateRequest.dest_country)
    names = [country["name_en"] for country in data["countries"]]
    await message.answer("Destination country?", reply_markup=choices_keyboard(names))


@router.message(CreateRequest.dest_country)
async def set_dest_country(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    country = country_by_label(data["countries"], message.text or "")
    if country is None:
        await message.answer("Choose a country from the list.")
        return
    await state.update_data(destination_country=country["code"], dest_cities=country.get("cities") or [])
    await state.set_state(CreateRequest.dest_city)
    cities = [city["name_en"] for city in country.get("cities") or []]
    await message.answer("Destination city?", reply_markup=choices_keyboard(cities))


@router.message(CreateRequest.dest_city)
async def set_dest_city(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    city = city_by_label(data.get("dest_cities") or [], message.text or "")
    if city is None:
        await message.answer("Choose a city from the list.")
        return
    if data.get("origin_country") == data.get("destination_country") and data.get("origin_city") == city["slug"]:
        await message.answer("Destination must be different from origin.")
        return
    await state.update_data(destination_city=city["slug"])
    data = await state.get_data()
    if data["type"] == "DEMAND":
        await state.set_state(CreateRequest.desired_date)
        await message.answer(
            "Desired send date? Tap Today or send YYYY-MM-DD.",
            reply_markup=choices_keyboard([TODAY], columns=1),
        )
        return
    await state.set_state(CreateRequest.flight_date)
    await message.answer(
        "Flight date? Tap Today or send YYYY-MM-DD.",
        reply_markup=choices_keyboard([TODAY], columns=1),
    )


@router.message(CreateRequest.desired_date)
async def set_desired_date(message: Message, state: FSMContext) -> None:
    try:
        value = parse_date(message.text or "")
    except ValueError:
        await message.answer("Use Today or a date like 2027-09-07.")
        return
    await state.update_data(desired_date=value)
    await state.set_state(CreateRequest.amount)
    await message.answer("Weight in kg?", reply_markup=cancel_keyboard())


@router.message(CreateRequest.flight_date)
async def set_flight_date(message: Message, state: FSMContext) -> None:
    try:
        value = parse_date(message.text or "")
    except ValueError:
        await message.answer("Use Today or a date like 2027-09-10.")
        return
    await state.update_data(flight_date=value)
    await state.set_state(CreateRequest.date_from)
    await message.answer(
        "Earliest date you can carry? Tap Today or send YYYY-MM-DD.",
        reply_markup=choices_keyboard([TODAY], columns=1),
    )


@router.message(CreateRequest.date_from)
async def set_date_from(message: Message, state: FSMContext) -> None:
    try:
        value = parse_date(message.text or "")
    except ValueError:
        await message.answer("Use Today or a date like 2027-09-01.")
        return
    await state.update_data(date_from=value)
    await state.set_state(CreateRequest.date_to)
    await message.answer(
        "Latest date you can carry? Tap Today or send YYYY-MM-DD.",
        reply_markup=choices_keyboard([TODAY, value], columns=2),
    )


@router.message(CreateRequest.date_to)
async def set_date_to(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        value = parse_date(message.text or "")
    except ValueError:
        await message.answer("Use Today or a date like 2027-09-15.")
        return
    if value < data["date_from"]:
        await message.answer("End date must be on or after the start date.")
        return
    await state.update_data(date_to=value)
    await state.set_state(CreateRequest.amount)
    await message.answer("Capacity in kg?", reply_markup=cancel_keyboard())


@router.message(CreateRequest.amount)
async def set_amount(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    try:
        amount = float(raw)
    except ValueError:
        await message.answer("Send a number, for example 2 or 5.5")
        return
    if amount < 0.01 or amount > 50:
        await message.answer("Must be between 0.01 and 50 kg.")
        return
    data = await state.get_data()
    if data["type"] == "DEMAND":
        await state.update_data(weight_kg=f"{amount:.2f}")
    else:
        await state.update_data(capacity_kg=f"{amount:.2f}")
    await state.set_state(CreateRequest.categories)
    allow_skip = data["type"] == "SUPPLY"
    prompt = "Select categories, then Done."
    if allow_skip:
        prompt = "What can you carry? Tap items, then Done. Or Skip."
    await message.answer(
        prompt,
        reply_markup=category_keyboard(data["categories"], [], allow_skip=allow_skip),
    )


@router.callback_query(StateFilter(CreateRequest.categories, CreateRequest.exclusions), F.data.startswith("cat:"))
async def toggle_category(callback: CallbackQuery, state: FSMContext) -> None:
    current = await state.get_state()
    data = await state.get_data()
    action = (callback.data or "").removeprefix("cat:")
    field = "selected_categories" if current == CreateRequest.categories.state else "selected_exclusions"
    selected = list(data.get(field) or [])
    allow_skip = data["type"] == "SUPPLY" or current == CreateRequest.exclusions.state

    if action == "skip":
        await state.update_data(**{field: []})
        await callback.answer()
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=None)
        await _after_category_step(callback, state, current)
        return

    if action == "done":
        if current == CreateRequest.categories.state and data["type"] == "DEMAND" and not selected:
            await callback.answer("Select at least one category.", show_alert=True)
            return
        await callback.answer()
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=None)
        await _after_category_step(callback, state, current)
        return

    if action in selected:
        selected.remove(action)
    else:
        selected.append(action)
    await state.update_data(**{field: selected})
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=category_keyboard(data["categories"], selected, allow_skip=allow_skip),
        )
    await callback.answer()


async def _after_category_step(callback: CallbackQuery, state: FSMContext, current: str | None) -> None:
    message = callback.message
    if message is None or not isinstance(message, Message):
        return
    if current == CreateRequest.categories.state:
        data = await state.get_data()
        if data["type"] == "SUPPLY":
            await state.set_state(CreateRequest.exclusions)
            await message.answer(
                "Will NOT carry? Tap items, then Done. Or Skip.",
                reply_markup=category_keyboard(data["categories"], [], allow_skip=True),
            )
            return
    await state.set_state(CreateRequest.description)
    await message.answer("Description? Send text or tap Skip.", reply_markup=choices_keyboard([SKIP], columns=1))


@router.message(CreateRequest.description)
async def set_description(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text != SKIP:
        await state.update_data(description=text)
    else:
        await state.update_data(description="")
    data = await state.get_data()
    await state.set_state(CreateRequest.confirm)
    await message.answer(_review_text(data), reply_markup=confirm_keyboard())


@router.message(CreateRequest.confirm, F.text == SUBMIT)
async def submit_request(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    user = message.from_user
    if user is None:
        await message.answer("Could not read your Telegram user.")
        return
    payload = {
        "type": data["type"],
        "origin_country": data["origin_country"],
        "origin_city": data["origin_city"],
        "destination_country": data["destination_country"],
        "destination_city": data["destination_city"],
        "item_category_codes": data.get("selected_categories") or [],
        "description": data.get("description") or "",
    }
    if data["type"] == "DEMAND":
        payload["desired_date"] = data["desired_date"]
        payload["weight_kg"] = data["weight_kg"]
    else:
        payload["flight_date"] = data["flight_date"]
        payload["date_from"] = data["date_from"]
        payload["date_to"] = data["date_to"]
        payload["capacity_kg"] = data["capacity_kg"]
        payload["excluded_category_codes"] = data.get("selected_exclusions") or []
    try:
        created = create_request(user.id, payload)
    except BotApiError as exc:
        logger.exception("Bot request create failed")
        await message.answer(f"Could not save the request. {exc}")
        return
    await state.clear()
    kind = "Send" if data["type"] == "DEMAND" else "Traveler"
    await message.answer(
        f"{kind} request #{created.get('id')} is active. Matching runs automatically.",
        reply_markup=main_reply_keyboard(),
    )


@router.message(CreateRequest.confirm)
async def confirm_help(message: Message) -> None:
    await message.answer("Tap Submit to save, or Cancel to stop.")


@router.message(CreateRequest.categories)
@router.message(CreateRequest.exclusions)
async def category_text_help(message: Message) -> None:
    await message.answer("Use the buttons under the previous message, then Done.")


def _review_text(data: dict) -> str:
    kind = "Send request" if data["type"] == "DEMAND" else "Traveler request"
    kg = data.get("weight_kg") or data.get("capacity_kg")
    categories = ", ".join(data.get("selected_categories") or []) or "—"
    if data["type"] == "DEMAND":
        dates = data["desired_date"]
    else:
        dates = f"flight {data['flight_date']}, carry {data['date_from']} to {data['date_to']}"
    lines = [
        f"Review {kind}:",
        f"{data['origin_city']} → {data['destination_city']}",
        dates,
        f"{kg} kg",
        f"Categories: {categories}",
    ]
    if data["type"] == "SUPPLY" and data.get("selected_exclusions"):
        lines.append("Will not carry: " + ", ".join(data["selected_exclusions"]))
    if data.get("description"):
        lines.append(data["description"])
    lines.append("\nTap Submit to save.")
    return "\n".join(lines)
