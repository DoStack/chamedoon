"""Courier bot handlers: every private message and every «cr:» button goes to the backend's courier flow.

The backend (POST /api/bot/courier/update/) answers with Bot API actions; this bot runs them.
Production does not run this polling bot: the Django webhook runs the same flow there.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, ChatMemberUpdated, LinkPreviewOptions, Message

from api_client import BotApiError, courier_update
from keyboards import to_markup

logger = logging.getLogger(__name__)
router = Router()
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
BACKEND_DOWN = "الان به سرور چمدون دسترسی نداریم. چند دقیقه دیگر دوباره امتحان کنید 🙏"


def _dump(item) -> dict:
    return item.model_dump(mode="json", by_alias=True, exclude_none=True)


async def run_actions(bot: Bot, actions: list[dict]) -> None:
    for action in actions:
        method = action.get("method")
        payload = dict(action.get("payload") or {})
        markup = to_markup(payload.get("reply_markup"))
        try:
            if method == "sendMessage":
                await bot.send_message(
                    chat_id=payload["chat_id"],
                    text=payload["text"],
                    parse_mode=payload.get("parse_mode"),
                    link_preview_options=NO_PREVIEW,
                    reply_markup=markup,
                )
            elif method == "editMessageText":
                try:
                    await bot.edit_message_text(
                        text=payload["text"],
                        chat_id=payload["chat_id"],
                        message_id=payload["message_id"],
                        parse_mode=payload.get("parse_mode"),
                        link_preview_options=NO_PREVIEW,
                        reply_markup=markup,
                    )
                except TelegramBadRequest as exc:
                    if "not modified" not in str(exc):
                        await bot.send_message(
                            chat_id=payload["chat_id"],
                            text=payload["text"],
                            parse_mode=payload.get("parse_mode"),
                            link_preview_options=NO_PREVIEW,
                            reply_markup=markup,
                        )
            elif method == "answerCallbackQuery":
                await bot.answer_callback_query(
                    callback_query_id=payload["callback_query_id"],
                    text=payload.get("text"),
                    show_alert=payload.get("show_alert"),
                )
        except Exception:  # noqa: BLE001 - one failed action must not stop the rest
            logger.exception("Courier bot action %s failed", method)


async def _relay(bot: Bot, update: dict) -> list[dict] | None:
    try:
        return await asyncio.to_thread(courier_update, update)
    except BotApiError:
        logger.exception("Courier flow unavailable")
        return None


@router.message(F.chat.type == "private")
async def on_message(message: Message, bot: Bot) -> None:
    actions = await _relay(bot, {"message": _dump(message)})
    if actions is None:
        await message.answer(BACKEND_DOWN)
        return
    await run_actions(bot, actions)


@router.callback_query(F.data.startswith("cr:"))
async def on_button(callback: CallbackQuery, bot: Bot) -> None:
    actions = await _relay(bot, {"callback_query": _dump(callback)})
    if actions is None:
        await callback.answer(BACKEND_DOWN, show_alert=True)
        return
    await run_actions(bot, actions)


@router.my_chat_member()
async def log_channel_membership(event: ChatMemberUpdated) -> None:
    chat = event.chat
    if chat.type not in {"channel", "supergroup"}:
        return
    logger.info(
        "Bot channel membership: type=%s id=%s username=%s status=%s",
        chat.type,
        chat.id,
        chat.username or "",
        event.new_chat_member.status,
    )
