from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import ChatMemberUpdated, Message

from api_client import BotApiError, sync_user, user_summary
from flows import start_create_flow
from keyboards import (
    BTN_BROWSE,
    BTN_CARRY,
    BTN_MATCHES,
    BTN_REQUESTS,
    BTN_SEND,
    main_reply_keyboard,
    open_mini_app_inline,
)
from config import mini_app_https_url

logger = logging.getLogger(__name__)
router = Router()

WELCOME = (
    "Welcome to Koolbar 👋\n\n"
    "C2C Cross-Border Courier\n\n"
    "What do you want to do?"
)


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    _sync_telegram_user(message)
    extra = ""
    if not mini_app_https_url():
        extra = "\n\nCreate send/carry requests here in chat. Mini App buttons need a public HTTPS URL."
    await message.answer(WELCOME + extra, reply_markup=main_reply_keyboard())


@router.message(Command("send"))
@router.message(F.text == BTN_SEND)
async def need_to_send(message: Message, state: FSMContext) -> None:
    _sync_telegram_user(message)
    await start_create_flow(message, state, "DEMAND")


@router.message(Command("carry"))
@router.message(F.text == BTN_CARRY)
async def can_carry(message: Message, state: FSMContext) -> None:
    _sync_telegram_user(message)
    await start_create_flow(message, state, "SUPPLY")


@router.message(Command("requests"))
@router.message(F.text == BTN_REQUESTS)
async def my_requests(message: Message) -> None:
    _sync_telegram_user(message)
    summary = _safe_summary(message)
    count = summary.get("active_request_count", 0)
    text = f"You have {count} active request(s)."
    https_url = mini_app_https_url("requests")
    if https_url:
        await message.answer(text + "\nOpen Koolbar to view them.", reply_markup=open_mini_app_inline("requests", "📋 My Requests"))
        return
    await message.answer(text + "\nOn this computer open http://localhost:3000/app/requests")


@router.message(Command("matches"))
@router.message(F.text == BTN_MATCHES)
async def my_matches(message: Message) -> None:
    _sync_telegram_user(message)
    summary = _safe_summary(message)
    count = summary.get("match_count", 0)
    text = f"You have {count} match(es) to review."
    https_url = mini_app_https_url("matches")
    if https_url:
        await message.answer(text + "\nOpen Koolbar to continue.", reply_markup=open_mini_app_inline("matches", "🤝 My Matches"))
        return
    await message.answer(text + "\nOn this computer open http://localhost:3000/app/matches")


@router.message(Command("browse"))
@router.message(F.text == BTN_BROWSE)
async def browse_requests(message: Message) -> None:
    _sync_telegram_user(message)
    text = "Browse open send and carry requests and propose a match yourself."
    https_url = mini_app_https_url("explore")
    if https_url:
        await message.answer(text, reply_markup=open_mini_app_inline("explore", "🔍 Browse requests"))
        return
    await message.answer(text + "\nOn this computer open http://localhost:3000/app/explore")


def _sync_telegram_user(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    try:
        sync_user(
            telegram_user_id=user.id,
            first_name=user.first_name or "Telegram",
            last_name=user.last_name,
            telegram_username=user.username,
        )
    except BotApiError:
        logger.exception("Could not sync Telegram user %s", user.id)


def _safe_summary(message: Message) -> dict:
    user = message.from_user
    if user is None:
        return {}
    try:
        return user_summary(user.id)
    except BotApiError:
        logger.exception("Could not load summary for Telegram user %s", user.id)
        return {}


@router.my_chat_member()
async def log_channel_membership(event: ChatMemberUpdated) -> None:
    chat = event.chat
    if chat.type not in {"channel", "supergroup"}:
        return
    status = event.new_chat_member.status
    logger.info(
        "Bot channel membership: type=%s id=%s username=%s status=%s",
        chat.type,
        chat.id,
        chat.username or "",
        status,
    )
